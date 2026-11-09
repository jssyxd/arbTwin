"""Lighter L2/L3 + trade recorder for fill-probability work (validation T2).

Public channels only -- no API key, no signing, read-only.  Verified against
`https://mainnet.zklighter.elliot.ai` on 2026-11-10:

* ``GET /api/v1/orderBookDetails``            -> market_id / decimals / mark price
* ``GET /api/v1/orderBookOrders?market_id=&limit=250`` -> *order-level* depth
  (``order_index``, ``price``, ``remaining_base_amount``, ``order_expiry``).
  ``order_index`` is the venue transaction index, i.e. monotone in placement
  order, so "how much size sits ahead of me at my price" is measurable, not
  guessed.  This is the single most valuable field in the capture.
* ``GET /api/v1/exchangeStats``               -> 24h trade count / quote volume
* WS ``wss://mainnet.zklighter.elliot.ai/stream``, ``{"type":"subscribe",
  "channel": "<ch>/<market_id>"}`` with
  ``order_book``  (aggregated price/size levels + ``offset`` + ``last_updated_at`` us),
  ``market_stats``(mark/index/mid/best bid-ask/funding/OI/24h volume),
  ``trade``       (``trades`` + ``liquidation_trades``, each with ``timestamp`` ms,
                   ``nonce``, ``block_height``, ``size``, ``price``, ``is_maker_ask``).

Outputs (one JSONL stream per channel, plus tidy Parquet for the ones that are
naturally tabular)::

    <out>/meta.json            run parameters, resolved market_ids, coverage
    <out>/book.jsonl           WS order_book  (top ``--top`` levels + aggregates)
    <out>/stats.jsonl          WS market_stats (+ .parquet)
    <out>/trades.jsonl         WS trade prints, one row per print (+ .parquet)
    <out>/book_orders.jsonl    REST order-level depth polls (+ orders/levels .parquet)
    <out>/book_top.parquet     book.jsonl flattened to level 1..10 columns (derived)

Every row carries ``recv_ts`` (local UTC seconds, float) *and* the venue's own
timestamp, so feed latency can be measured instead of assumed.

Usage::

    python validation/fill/record_lighter.py --symbols ETH,BTC,COIN,VVV --minutes 6
    python validation/fill/record_lighter.py --symbols ETH --minutes 1 --no-parquet
    python validation/fill/record_lighter.py --list            # what is recordable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

BASE_URL = "https://mainnet.zklighter.elliot.ai"
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
UA = "arbtwin-validation/0.1 (fill-probability research; read-only)"
PERP = "perp"


# --------------------------------------------------------------------------- http
def http_json(path: str, params: Optional[dict] = None, base_url: str = BASE_URL,
              timeout: float = 20.0, retries: int = 2) -> Any:
    url = base_url + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Observed 2026-11-10: polling /orderBookOrders for 4 markets every 2 s
            # returns HTTP 405 "Not Allowed" after ~4 min -- that is Lighter's edge
            # refusing an abusive client, not a dead endpoint (the same URL works
            # again after a pause).  Retry is exactly wrong here; back off.
            if exc.code in (405, 429, 503):
                raise RateLimited(f"HTTP {exc.code} on {url}") from exc
            last = exc
        except Exception as exc:                      # noqa: BLE001 - network, log and retry
            last = exc
        time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {type(last).__name__}: {last}")


class RateLimited(RuntimeError):
    """The venue told us to slow down (405/429/503).  Callers must back off."""


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- markets
@dataclass
class Market:
    symbol: str
    market_id: int
    price_decimals: int
    size_decimals: int
    mark_price: float
    min_base: float
    min_quote: float
    status: str
    daily_trades: int = 0
    daily_quote_volume: float = 0.0

    @property
    def tick(self) -> float:
        return 10.0 ** -self.price_decimals

    def tick_bps(self) -> float:
        return self.tick / self.mark_price * 1e4 if self.mark_price else float("inf")


def resolve_markets(symbols: list[str] | None = None, base_url: str = BASE_URL) -> list[Market]:
    """market_id resolution is *not* guessable: it comes from orderBookDetails."""
    details = http_json("/api/v1/orderBookDetails", base_url=base_url).get("order_book_details") or []
    try:
        stats = {str(s.get("symbol", "")).upper(): s
                 for s in http_json("/api/v1/exchangeStats", base_url=base_url).get("order_book_stats", [])}
    except Exception:                                  # noqa: BLE001 - volume is decoration
        stats = {}
    out: list[Market] = []
    for item in details:
        if item.get("market_type") != PERP:
            continue
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        if symbols and symbol not in {s.upper() for s in symbols}:
            continue
        st = stats.get(symbol, {})
        out.append(Market(
            symbol=symbol,
            market_id=int(item.get("market_id")),
            price_decimals=int(item.get("price_decimals") or item.get("supported_price_decimals") or 2),
            size_decimals=int(item.get("size_decimals") or item.get("supported_size_decimals") or 4),
            mark_price=_f(item.get("mark_price") or item.get("last_trade_price")),
            min_base=_f(item.get("min_base_amount")),
            min_quote=_f(item.get("min_quote_amount")),
            status=str(item.get("status", "")),
            daily_trades=int(st.get("daily_trades_count") or 0),
            daily_quote_volume=_f(st.get("daily_quote_token_volume")),
        ))
    wanted = {s.upper() for s in (symbols or [])}
    missing = sorted(wanted - {m.symbol for m in out})
    if missing:
        print(f"[record] WARNING: not found / not perp: {missing}", file=sys.stderr)
    return sorted(out, key=lambda m: m.market_id)


# --------------------------------------------------------------------------- writer
class JsonlWriter:
    """Append-only JSONL with its own lock; one file per channel."""

    def __init__(self, path: str, buffer_lines: int = 200):
        self.path = path
        self.n = 0
        self.bytes = 0
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._buffer_lines = buffer_lines
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, row: dict) -> None:
        line = json.dumps(row, separators=(",", ":"), default=str)
        with self._lock:
            self._buf.append(line)
            self.n += 1
            self.bytes += len(line) + 1
            if len(self._buf) >= self._buffer_lines:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if self._buf:
            self._fh.write("\n".join(self._buf) + "\n")
            self._buf.clear()
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            self._fh.close()


# --------------------------------------------------------------------------- recorder
class LighterRecorder:
    """WS (order_book / market_stats / trade) + REST order-level depth poller."""

    def __init__(self, markets: list[Market], out_dir: str, poll_s: float = 2.0,
                 top: int = 25, depth_limit: int = 250, orders_bps: float = 60.0,
                 base_url: str = BASE_URL, ws_url: str = WS_URL, verbose: bool = True):
        self.markets = {m.market_id: m for m in markets}
        self.out_dir = out_dir
        self.poll_s = poll_s
        self.top = top
        self.depth_limit = depth_limit
        self.orders_bps = orders_bps
        self.base_url = base_url
        self.ws_url = ws_url
        self.verbose = verbose
        os.makedirs(out_dir, exist_ok=True)
        self.writers = {
            "book": JsonlWriter(os.path.join(out_dir, "book.jsonl")),
            "stats": JsonlWriter(os.path.join(out_dir, "stats.jsonl")),
            "trades": JsonlWriter(os.path.join(out_dir, "trades.jsonl")),
            "book_orders": JsonlWriter(os.path.join(out_dir, "book_orders.jsonl")),
        }
        self.counters: dict[str, int] = {"book": 0, "stats": 0, "trades": 0, "book_orders": 0,
                                         "liq_trades": 0, "ws_connects": 0, "ws_errors": 0,
                                         "poll_errors": 0, "poll_rate_limited": 0, "dropped": 0}
        self._poll_paused_until = 0.0
        self._poll_backoff_s = 0.0
        self._clock = threading.Lock()
        self._stop = threading.Event()
        self.t_start = 0.0
        self.t_end = 0.0
        self._last_seq: dict[int, int] = {}

    # -- helpers --------------------------------------------------------------
    def _bump(self, key: str, n: int = 1) -> None:
        with self._clock:
            self.counters[key] = self.counters.get(key, 0) + n

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[record {time.strftime('%H:%M:%S')}] {msg}", flush=True)

    @staticmethod
    def _levels(rows: list[dict], top: int) -> tuple[list[list[float]], float, int]:
        """[(price, size)] -> trimmed list, total size, n_levels."""
        out = [[_f(r.get("price")), _f(r.get("size") or r.get("remaining_base_amount"))] for r in rows[:top]]
        return out, sum(_f(r.get("size") or r.get("remaining_base_amount")) for r in rows), len(rows)

    # -- websocket ------------------------------------------------------------
    async def _ws_loop(self) -> None:
        import websockets

        ids = sorted(self.markets)
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, close_timeout=2,
                                              max_queue=4096) as ws:
                    self._bump("ws_connects")
                    for mid in ids:
                        for ch in (f"order_book/{mid}", f"market_stats/{mid}", f"trade/{mid}"):
                            await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                    self._log(f"ws connected, subscribed {len(ids)} markets x 3 channels")
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=25)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({"type": "ping"}))
                            continue
                        self._ingest(json.loads(raw), time.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:                      # noqa: BLE001 - reconnect
                self._bump("ws_errors")
                if not self._stop.is_set():
                    self._log(f"ws error {type(exc).__name__}: {exc}; reconnect in 1.5s")
                    await asyncio.sleep(1.5)

    def _ingest(self, msg: dict, recv_ts: float) -> None:
        channel = str(msg.get("channel", ""))
        if ":" not in channel:
            return                                     # session/ping/pong frames
        name, _, raw_id = channel.partition(":")
        try:
            mid = int(raw_id)
        except ValueError:
            return
        mkt = self.markets.get(mid)
        if mkt is None:
            self._bump("dropped")
            return
        if name == "order_book":
            self._on_book(msg, recv_ts, mkt)
        elif name == "market_stats":
            self._on_stats(msg, recv_ts, mkt)
        elif name == "trade":
            self._on_trades(msg, recv_ts, mkt)

    def _on_book(self, msg: dict, recv_ts: float, mkt: Market) -> None:
        ob = msg.get("order_book") or {}
        bids_raw = ob.get("bids") or []
        asks_raw = ob.get("asks") or []
        bids, bid_total, n_bid = self._levels(bids_raw, self.top)
        asks, ask_total, n_ask = self._levels(asks_raw, self.top)
        offset = msg.get("offset")
        prev = self._last_seq.get(mkt.market_id)
        gap = (offset - prev) if (prev is not None and isinstance(offset, int)) else None
        if isinstance(offset, int):
            self._last_seq[mkt.market_id] = offset
        bb = bids[0][0] if bids else 0.0
        ba = asks[0][0] if asks else 0.0
        mid = (bb + ba) / 2 if bb and ba else 0.0
        venue_ts_ms = _f(msg.get("timestamp"))
        last_upd_us = _f(msg.get("last_updated_at"))
        row = {
            "recv_ts": recv_ts, "symbol": mkt.symbol, "market_id": mkt.market_id,
            "venue_ts_ms": venue_ts_ms, "last_updated_us": last_upd_us, "offset": offset,
            "offset_gap": gap, "src": "ws",
            "bid": bb, "ask": ba, "mid": mid,
            "bid_sz": bids[0][1] if bids else 0.0, "ask_sz": asks[0][1] if asks else 0.0,
            "spread_bps": (ba - bb) / mid * 1e4 if mid else None,
            "bids": bids, "asks": asks,
            "bid_total_top": bid_total, "ask_total_top": ask_total,
            "n_bid_levels": n_bid, "n_ask_levels": n_ask,
            "latency_ms": (recv_ts * 1000 - last_upd_us / 1000) if last_upd_us else None,
        }
        self.writers["book"].write(row)
        self._bump("book")

    def _on_stats(self, msg: dict, recv_ts: float, mkt: Market) -> None:
        ms = msg.get("market_stats") or {}
        self.writers["stats"].write({
            "recv_ts": recv_ts, "symbol": mkt.symbol, "market_id": mkt.market_id,
            "venue_ts_ms": _f(msg.get("timestamp")), "src": "ws",
            "mark": _f(ms.get("mark_price")), "index": _f(ms.get("index_price")),
            "mid": _f(ms.get("mid_price")), "best_bid": _f(ms.get("best_bid_price")),
            "best_ask": _f(ms.get("best_ask_price")), "last": _f(ms.get("last_trade_price")),
            "oi_usd": _f(ms.get("open_interest_usd")),
            "funding_rate_pct_h": _f(ms.get("current_funding_rate") or ms.get("funding_rate")),
            "funding_ts_ms": _f(ms.get("funding_timestamp")),
            "premium": _f(ms.get("premium")),
            "daily_quote_volume": _f(ms.get("daily_quote_token_volume")),
            "daily_trades": _f(ms.get("daily_trades_count")),
        })
        self._bump("stats")

    def _on_trades(self, msg: dict, recv_ts: float, mkt: Market) -> None:
        nonce = msg.get("nonce")
        for kind, key in (("trade", "trades"), ("liquidation", "liquidation_trades")):
            for t in msg.get(key) or []:
                if int(t.get("market_id", mkt.market_id)) != mkt.market_id:
                    continue
                self.writers["trades"].write({
                    "recv_ts": recv_ts, "symbol": mkt.symbol, "market_id": mkt.market_id,
                    "venue_ts_ms": _f(t.get("timestamp")), "kind": kind, "src": "ws",
                    "trade_id": t.get("trade_id"), "nonce": nonce,
                    "block_height": t.get("block_height"),
                    "px": _f(t.get("price")), "sz": _f(t.get("size")),
                    "usd": _f(t.get("usd_amount")),
                    "is_maker_ask": t.get("is_maker_ask"),
                    "taker_fee": t.get("taker_fee"),
                    "maker_order_id": t.get("ask_id") if t.get("is_maker_ask") else t.get("bid_id"),
                })
                self._bump("trades")
                if kind == "liquidation":
                    self._bump("liq_trades")

    # -- rest order-level depth ------------------------------------------------
    def _poll_loop(self) -> None:
        """Poll every market in parallel: one REST call is ~1.4 s here, so a
        sequential loop would turn `--poll-s 2` into a 9 s cadence for 5 markets
        and blur the order-level cancel/placement measurement.

        A 405/429 pauses *all* polling for a backoff window (the limit is per
        client, not per market) and the window doubles on each repeat, up to 60 s.
        WS is untouched: the book and trade tape keep recording."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, len(self.markets))) as pool:
            while not self._stop.is_set():
                t_poll = time.time()
                if t_poll < self._poll_paused_until:
                    self._stop.wait(min(5.0, self._poll_paused_until - t_poll))
                    continue
                list(pool.map(lambda kv: self._poll_one(kv[0], kv[1]), self.markets.items()))
                self._stop.wait(max(0.0, self.poll_s - (time.time() - t_poll)))

    def _backoff(self, exc: Exception, mkt: "Market") -> None:
        if isinstance(exc, RateLimited):
            self._poll_backoff_s = min(60.0, (self._poll_backoff_s or self.poll_s) * 2)
            self._poll_paused_until = time.time() + self._poll_backoff_s
            self._bump("poll_rate_limited")
            self._log(f"rate limited on {mkt.symbol}: pausing depth polls {self._poll_backoff_s:.0f}s "
                      f"(WS unaffected)")

    def _poll_one(self, mid: int, mkt: Market) -> None:
        if self._stop.is_set():
            return
        recv_ts = time.time()
        try:
            data = http_json("/api/v1/orderBookOrders",
                             params={"market_id": mid, "limit": self.depth_limit},
                             base_url=self.base_url, timeout=10.0, retries=1)
        except Exception as exc:                   # noqa: BLE001
            self._bump("poll_errors")
            self._backoff(exc, mkt)
            return
        self._poll_backoff_s = 0.0
        asks = data.get("asks") or []
        bids = data.get("bids") or []
        bb = _f(bids[0].get("price")) if bids else 0.0
        ba = _f(asks[0].get("price")) if asks else 0.0
        midpx = (bb + ba) / 2 if bb and ba else mkt.mark_price
        band = midpx * self.orders_bps / 1e4

        def compact(rows: list[dict], side: int) -> list[list[Any]]:
            out = []
            for o in rows:
                px = _f(o.get("price"))
                if not px or abs(px - midpx) > band:
                    continue
                out.append([int(o.get("order_index") or 0), px,
                            _f(o.get("remaining_base_amount")),
                            _f(o.get("initial_base_amount")), side])
            return out

        def levels_agg(rows: list[dict]) -> list[list[Any]]:
            agg: dict[float, list[float]] = {}
            for o in rows:
                px = _f(o.get("price"))
                sz = _f(o.get("remaining_base_amount"))
                oi = int(o.get("order_index") or 0)
                a = agg.setdefault(px, [0.0, 0, oi, oi])
                a[0] += sz
                a[1] += 1
                a[2] = min(a[2], oi) if oi else a[2]
                a[3] = max(a[3], oi)
            return [[px, round(v[0], 8), v[1], v[2], v[3]] for px, v in sorted(agg.items())]

        self.writers["book_orders"].write({
            "recv_ts": recv_ts, "symbol": mkt.symbol, "market_id": mid, "src": "rest",
            "venue_ts_ms": _f(data.get("timestamp")) or None,
            "bid": bb, "ask": ba, "mid": midpx, "orders_bps": self.orders_bps,
            "total_asks": data.get("total_asks"), "total_bids": data.get("total_bids"),
            "n_ask_rows": len(asks), "n_bid_rows": len(bids),
            "ask_bid_sum": round(sum(_f(o.get("remaining_base_amount")) for o in asks), 8),
            "bid_bid_sum": round(sum(_f(o.get("remaining_base_amount")) for o in bids), 8),
            "max_order_index": max([int(o.get("order_index") or 0) for o in (asks + bids)] or [0]),
            # [order_index, price, remaining, initial, side(1=ask/-1=bid)] within +/-orders_bps
            "orders": compact(asks, 1) + compact(bids, -1),
            # per-price aggregate over the full 250 rows: [px, size, n_orders, min_idx, max_idx]
            "ask_levels": levels_agg(asks), "bid_levels": levels_agg(bids),
        })
        self._bump("book_orders")

    # -- lifecycle -------------------------------------------------------------
    def run(self, minutes: float) -> dict:
        self.t_start = time.time()
        deadline = self.t_start + minutes * 60
        poller = threading.Thread(target=self._poll_loop, name="lighter-depth-poll", daemon=True)
        poller.start()
        loop = asyncio.new_event_loop()
        ws_thread = threading.Thread(target=lambda: loop.run_until_complete(self._ws_loop()),
                                     name="lighter-ws", daemon=True)
        ws_thread.start()
        try:
            while time.time() < deadline and not self._stop.is_set():
                time.sleep(5)
                self._log(" ".join(f"{k}={v}" for k, v in sorted(self.counters.items()) if v))
        except KeyboardInterrupt:
            self._log("interrupted")
        finally:
            self._stop.set()
            self.t_end = time.time()
            time.sleep(0.6)
            for w in self.writers.values():
                w.close()
        return self.summary()

    def summary(self) -> dict:
        dur = max(1e-9, self.t_end - self.t_start) if self.t_end else 0.0
        per_market = {}
        for mid, mkt in self.markets.items():
            per_market[mkt.symbol] = {"market_id": mid, "tick_bps": round(mkt.tick_bps(), 4),
                                      "price_decimals": mkt.price_decimals,
                                      "size_decimals": mkt.size_decimals,
                                      "min_base": mkt.min_base, "min_quote": mkt.min_quote,
                                      "daily_trades": mkt.daily_trades,
                                      "daily_quote_volume_usd": mkt.daily_quote_volume}
        return {
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.t_start)) if self.t_start else None,
            "t_start": self.t_start, "t_end": self.t_end, "duration_s": round(dur, 2),
            "base_url": self.base_url, "ws_url": self.ws_url,
            "poll_s": self.poll_s, "top_levels": self.top, "depth_limit": self.depth_limit,
            "orders_bps": self.orders_bps,
            "counters": dict(self.counters),
            "rates_per_s": {k: round(v / dur, 3) for k, v in self.counters.items() if v},
            "files": {k: {"path": w.path, "rows": w.n, "bytes": w.bytes} for k, w in self.writers.items()},
            "markets": per_market,
        }


# --------------------------------------------------------------------------- parquet
def to_parquet(out_dir: str) -> dict[str, str]:
    """Tidy Parquet for the tabular streams.  Requires pandas + pyarrow."""
    import pandas as pd

    written: dict[str, str] = {}
    plans = [("stats", "stats.parquet"), ("trades", "trades.parquet")]
    for name, target in plans:
        src = os.path.join(out_dir, f"{name}.jsonl")
        if not os.path.exists(src) or os.path.getsize(src) == 0:
            continue
        rows = [json.loads(line) for line in open(src, encoding="utf-8") if line.strip()]
        if not rows:
            continue
        dst = os.path.join(out_dir, target)
        pd.DataFrame(rows).to_parquet(dst, index=False)
        written[name] = dst
    # tidy flat view of the book stream (levels 1..10 as columns) -- derived from
    # book.jsonl rather than written twice, so the JSONL stays the single source.
    src = os.path.join(out_dir, "book.jsonl")
    if os.path.exists(src) and os.path.getsize(src) > 0:
        flat = []
        for line in open(src, encoding="utf-8"):
            if not line.strip():
                continue
            d = json.loads(line)
            row = {"recv_ts": d["recv_ts"], "symbol": d["symbol"], "market_id": d["market_id"],
                   "venue_ts_ms": d.get("venue_ts_ms"), "offset": d.get("offset"),
                   "offset_gap": d.get("offset_gap"), "mid": d.get("mid"), "bid": d.get("bid"),
                   "ask": d.get("ask"), "bid_sz": d.get("bid_sz"), "ask_sz": d.get("ask_sz"),
                   "spread_bps": d.get("spread_bps"), "latency_ms": d.get("latency_ms"),
                   "n_bid_levels": d.get("n_bid_levels"), "n_ask_levels": d.get("n_ask_levels")}
            for tag in ("bids", "asks"):
                for i, lvl in enumerate((d.get(tag) or [])[:10]):
                    row[f"{tag[0]}{i + 1}_px"] = lvl[0]
                    row[f"{tag[0]}{i + 1}_sz"] = lvl[1]
            flat.append(row)
        if flat:
            written["book_top"] = os.path.join(out_dir, "book_top.parquet")
            pd.DataFrame(flat).to_parquet(written["book_top"], index=False)
    # order-level depth -> tidy long format (one row per resting order)
    src = os.path.join(out_dir, "book_orders.jsonl")
    if os.path.exists(src) and os.path.getsize(src) > 0:
        recs = []
        levels = []
        for line in open(src, encoding="utf-8"):
            if not line.strip():
                continue
            d = json.loads(line)
            key = {"recv_ts": d["recv_ts"], "symbol": d["symbol"], "market_id": d["market_id"],
                   "mid": d["mid"], "max_order_index": d.get("max_order_index", 0)}
            for oi, px, rem, init, side in d.get("orders") or []:
                recs.append({**key, "order_index": oi, "px": px, "remaining": rem,
                             "initial": init, "side": side})
            for side, rows_l in (("ask", d.get("ask_levels")), ("bid", d.get("bid_levels"))):
                for px, sz, n, mn, mx in rows_l or []:
                    levels.append({**key, "side": side, "px": px, "size": sz, "n_orders": n,
                                   "min_order_index": mn, "max_order_index": mx})
        if recs:
            written["orders"] = os.path.join(out_dir, "orders.parquet")
            pd.DataFrame(recs).to_parquet(written["orders"], index=False)
        if levels:
            written["levels"] = os.path.join(out_dir, "levels.parquet")
            pd.DataFrame(levels).to_parquet(written["levels"], index=False)
    return written


# --------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record Lighter L2/L3 + trades (public, read-only).")
    ap.add_argument("--symbols", default="ETH,BTC,COIN,VVV,LIT",
                    help="comma-separated Lighter perp symbols (default: %(default)s)")
    ap.add_argument("--minutes", type=float, default=6.0, help="capture length (default: %(default)s)")
    ap.add_argument("--out", default=None, help="output dir (default: validation/fill/data/sample)")
    ap.add_argument("--poll-s", type=float, default=5.0,
                    help="orderBookOrders poll interval seconds (default: %(default)s; "
                         "2 s across 4 markets triggered HTTP 405 rate limiting after ~4 min)")
    ap.add_argument("--top", type=int, default=25, help="book levels kept per side in book.jsonl")
    ap.add_argument("--depth-limit", type=int, default=250, help="orderBookOrders limit (max 250)")
    ap.add_argument("--orders-bps", type=float, default=60.0,
                    help="keep order-level rows within this many bps of mid (levels are always full depth)")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--ws-url", default=WS_URL)
    ap.add_argument("--no-parquet", action="store_true")
    ap.add_argument("--list", action="store_true", help="print recordable perps with 24h activity and exit")
    args = ap.parse_args(argv)

    if args.list:
        mkts = resolve_markets(base_url=args.base_url)
        mkts.sort(key=lambda m: -m.daily_quote_volume)
        print(f"{'symbol':12}{'mid':>6}{'pdec':>5}{'sdec':>5}{'tick_bps':>10}{'mark':>14}"
              f"{'trades24':>10}{'qvol24$M':>11}  status")
        for m in mkts:
            print(f"{m.symbol:12}{m.market_id:>6}{m.price_decimals:>5}{m.size_decimals:>5}"
                  f"{m.tick_bps():>10.3f}{m.mark_price:>14.5f}{m.daily_trades:>10}"
                  f"{m.daily_quote_volume / 1e6:>11.2f}  {m.status}")
        return 0

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    markets = resolve_markets(symbols, base_url=args.base_url)
    if not markets:
        print("no markets resolved; nothing to record", file=sys.stderr)
        return 2
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sample")
    rec = LighterRecorder(markets, out_dir, poll_s=args.poll_s, top=args.top,
                          depth_limit=args.depth_limit, orders_bps=args.orders_bps,
                          base_url=args.base_url, ws_url=args.ws_url)

    def _sigint(_signum, _frame):
        rec._stop.set()
    try:
        signal.signal(signal.SIGINT, _sigint)
    except (ValueError, AttributeError):
        pass

    print(f"[record] markets: {[(m.symbol, m.market_id) for m in markets]}")
    print(f"[record] out={out_dir} minutes={args.minutes} poll_s={args.poll_s} top={args.top}")
    summary = rec.run(args.minutes)
    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    if not args.no_parquet:
        try:
            summary["parquet"] = to_parquet(out_dir)
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2)
        except Exception as exc:                          # noqa: BLE001 - parquet is optional
            print(f"[record] parquet conversion failed ({type(exc).__name__}: {exc}); JSONL is intact",
                  file=sys.stderr)
    print(json.dumps({k: summary[k] for k in ("duration_s", "counters", "rates_per_s", "files")},
                     indent=2))
    total_mb = sum(f["bytes"] for f in summary["files"].values()) / 1e6
    print(f"[record] wrote {total_mb:.1f} MB JSONL to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
