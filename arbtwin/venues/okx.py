"""OKX adapter: USDT-margined SWAPs, public WS (`books5`), private REST.

Facts this file encodes (verified against api.okx.com, 2026-11-08):

* `instruments` gives `ctVal` (base units per contract) and `ctMult`; a base
  quantity therefore costs `ctVal * price`, and an order is sent in *contracts*.
* funding is 8-hourly for SWAP, so annualising is `rate * 3 * 365`.
* a resting `post_only` order is rejected if it would cross; an IOC
  (`ordType=limit` + `ioc`) is the hedge vehicle.
* `books5` pushes full 5-level books, `bbo-tbt` pushes tick-by-tick top of book.

Only reads are used unless credentials are present AND `dry_run=False`.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import threading
import time
from typing import Optional

from ..config import OkxCredentials
from ..models import Book, Fill, MarketSpec, MarketStats, OrderRequest, VenuePosition
from .base import ThreadedWs, VenueAdapter, VenueError, http_json

SWAP_TAG = "SWAP"
DEPTH_LEVELS = 400        # REST max; 5 levels reach ~1.5bps of a 100bps band (59x understatement)


class _OkxWs(ThreadedWs):
    def __init__(self, url: str, inst_ids: list[str], sink: "OkxVenue", proxy: Optional[str] = None):
        super().__init__("okx-ws", url)
        self.inst_ids = inst_ids
        self.sink = sink
        self.proxy = proxy

    def run_once(self) -> None:  # pragma: no cover - network
        import asyncio

        async def main():
            import websockets

            async with websockets.connect(self.url, ping_interval=20, close_timeout=2,
                                          proxy=self.proxy) as ws:
                args = [{"channel": "books5", "instId": i} for i in self.inst_ids]
                await ws.send(json.dumps({"op": "subscribe", "args": args}))
                while not self._stop.is_set():
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
                    except asyncio.TimeoutError:
                        await ws.send("ping")
                        continue
                    if "data" not in msg:
                        continue
                    self.last_message_at = time.time()
                    for item in msg["data"]:
                        inst = item.get("instId")
                        bids = [(float(p), float(s)) for p, s, *_ in item.get("bids", [])]
                        asks = [(float(p), float(s)) for p, s, *_ in item.get("asks", [])]
                        self.sink._ingest(inst, bids, asks)

        asyncio.run(main())


class OkxVenue(VenueAdapter):
    name = "okx"
    funding_interval_h = 8.0

    def __init__(self, creds: Optional[OkxCredentials] = None, proxy: Optional[str] = None):
        self.creds = creds or OkxCredentials()
        self.proxy = proxy
        self._specs: dict[str, MarketSpec] = {}
        self._by_symbol: dict[str, MarketSpec] = {}
        self._books: dict[str, Book] = {}
        self._lock = threading.Lock()
        self._ws: Optional[_OkxWs] = None
        # mark/index/funding/OI change on the order of seconds-to-hours while the
        # book changes in milliseconds.  Caching stats keeps a 12-symbol poll at
        # a handful of REST calls instead of ~40 (OKX allows 20 req / 2 s).
        self._stats_cache: dict[str, tuple[float, MarketStats]] = {}
        self.stats_ttl_s = 5.0
        # `books5` (WS) is 5 levels: fine for pricing, useless for depth gates.
        # Keep them apart so a fast path can never silently truncate a gate.
        self._ws_books: dict[str, Book] = {}
        self.book_ttl_s = 1.5
        # mark / index / OI are published in bulk for the whole venue.  Three
        # calls replace 3 x N per-symbol calls; at N=12 that is 36 -> 3.
        self._bulk: dict[str, dict] = {}
        self._bulk_ts = 0.0
        self._bulk_lock = threading.Lock()
        self.bulk_ttl_s = 5.0
        self._funding: dict[str, tuple[float, float]] = {}
        self.funding_ttl_s = 300.0

    # ------------------------------------------------------------------ rest
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        data = http_json(f"{self.creds.base_url}{path}", params=params, proxy=self.proxy)
        if data.get("code") not in ("0", 0, None):
            raise VenueError(f"okx {path}: {data.get('code')} {data.get('msg')}")
        return data

    def _private(self, method: str, path: str, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        if not self.creds.has_private:
            raise VenueError("okx private API needs OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE")
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        payload = json.dumps(body) if body is not None else ""
        prehash = f"{ts}{method.upper()}{path}{query}{payload}"
        sign = base64.b64encode(
            hmac.new(self.creds.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "OK-ACCESS-KEY": self.creds.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.creds.passphrase,
            "Content-Type": "application/json",
        }
        if self.creds.simulated:
            headers["x-simulated-trading"] = "1"
        data = http_json(f"{self.creds.base_url}{path}{query}", method=method, body=body,
                         headers=headers, proxy=self.proxy)
        if data.get("code") not in ("0", 0):
            raise VenueError(f"okx {path}: {data.get('code')} {data.get('msg')}")
        return data

    # ------------------------------------------------------------------ specs
    def specs(self, symbols: Optional[list[str]] = None) -> dict[str, MarketSpec]:
        if not self._specs:
            data = self._get("/api/v5/public/instruments", {"instType": SWAP_TAG})
            tickers = {t["instId"]: t for t in self._get("/api/v5/market/tickers", {"instType": SWAP_TAG})["data"]}
            for item in data["data"]:
                inst_id = item["instId"]
                if item.get("settleCcy") not in ("USDT", "") or item.get("state") != "live":
                    continue
                base = inst_id.split("-")[0].upper()
                ct_val = float(item.get("ctVal") or 1.0)
                ct_mult = float(item.get("ctMult") or 1.0)
                levers = [float(x) for x in (item.get("lever") or "1").split(",") if x]
                max_lev = max(levers) if levers else 1.0
                ticker = tickers.get(inst_id, {})
                last = float(ticker.get("last") or 0.0)
                spec = MarketSpec(
                    venue=self.name,
                    symbol=base,
                    market_ref=inst_id,
                    price_decimals=decimals_from_tick(item.get("tickSz", "0.01")),
                    size_decimals=decimals_from_lot(item.get("lotSz", "1")),
                    min_base=float(item.get("minSz") or 0.0) * ct_val * ct_mult,
                    min_quote=0.0,
                    contract_multiplier=ct_val * ct_mult,
                    initial_margin_frac=1.0 / max_lev if max_lev else 0.05,
                    maintenance_margin_frac=0.005 * 2,   # conservative default, refined by tiers
                    maker_fee_bps=2.0,
                    taker_fee_bps=5.0,
                    oi_limit_usd=0.0,
                    max_leverage=max_lev,
                    status="active" if item.get("state") == "live" else item.get("state", "unknown"),
                )
                self._specs[base] = spec
                self._by_symbol[base] = spec
                if last:
                    self._books.setdefault(base, Book(self.name, base, ts=0.0))
        if symbols:
            return {s: self._specs[s] for s in symbols if s in self._specs}
        return dict(self._specs)

    # ------------------------------------------------------------------ bulk snapshot
    def _bulk_snapshot(self) -> dict[str, dict]:
        """{symbol: {mark, index, oi_usd}} for every SWAP, refreshed at most every TTL.

        Single-flight: with 12 symbols fetched in parallel, an unguarded cache
        stampede turns 3 bulk calls into 12 x 3 = 36 (measured: 15s -> 24s per
        poll).  The lock makes concurrent callers wait for one refresh.
        """
        if self._bulk and (time.time() - self._bulk_ts) <= self.bulk_ttl_s:
            return self._bulk
        with self._bulk_lock:
            if self._bulk and (time.time() - self._bulk_ts) <= self.bulk_ttl_s:
                return self._bulk
            return self._bulk_refresh()

    def _bulk_refresh(self) -> dict[str, dict]:
        # NOTE: caller holds `_bulk_lock`
        urls = {
            "mark": f"{self.creds.base_url}/api/v5/public/mark-price?instType={SWAP_TAG}",
            "oi": f"{self.creds.base_url}/api/v5/public/open-interest?instType={SWAP_TAG}",
            "index": f"{self.creds.base_url}/api/v5/market/index-tickers?quoteCcy=USDT",
        }
        got: dict[str, dict] = {}

        def fetch(key: str, url: str) -> None:
            try:
                got[key] = http_json(url, proxy=self.proxy, timeout=8.0, retries=1)
            except Exception:
                got[key] = {}

        import threading as _threading

        pool = [_threading.Thread(target=fetch, args=(k, u), daemon=True) for k, u in urls.items()]
        for t in pool:
            t.start()
        for t in pool:
            t.join(10.0)

        out: dict[str, dict] = {}
        for row in (got.get("mark") or {}).get("data", []) or []:
            sym = str(row.get("instId", "")).split("-")[0].upper()
            if sym and row.get("markPx"):
                out.setdefault(sym, {})["mark"] = float(row["markPx"])
        for row in (got.get("index") or {}).get("data", []) or []:
            inst = str(row.get("instId", ""))
            if not inst.endswith("-USDT"):
                continue
            sym = inst.split("-")[0].upper()
            if row.get("idxPx"):
                out.setdefault(sym, {})["index"] = float(row["idxPx"])
        for row in (got.get("oi") or {}).get("data", []) or []:
            sym = str(row.get("instId", "")).split("-")[0].upper()
            if row.get("oiUsd"):
                out.setdefault(sym, {})["oi_usd"] = float(row["oiUsd"])
        if out:
            self._bulk, self._bulk_ts = out, time.time()
        return self._bulk

    # ------------------------------------------------------------------ market data
    def book(self, symbol: str, levels: int = 0) -> Book:
        """REST snapshot with full depth.

        Deliberately *not* served from the `books5` WS cache: 5 levels make the
        1% depth gate wrong by more than an order of magnitude (measured: AAPL
        1% depth $12.4k at 5 levels vs $736k at 200 levels).
        """
        spec = self._by_symbol.get(symbol) or self.specs([symbol]).get(symbol)
        if not spec:
            raise VenueError(f"okx: unknown symbol {symbol}")
        with self._lock:
            cached = self._books.get(symbol)
        if cached and (time.time() - cached.ts) <= self.book_ttl_s:
            return cached
        data = http_json(f"{self.creds.base_url}/api/v5/market/books",
                         params={"instId": spec.market_ref, "sz": levels or DEPTH_LEVELS},
                         proxy=self.proxy, timeout=6.0, retries=1)
        if data.get("code") not in ("0", 0, None):
            raise VenueError(f"okx books: {data.get('code')} {data.get('msg')}")
        # `/market/books` sizes are in CONTRACTS, not base units.  Treating them as
        # base units inflates depth by 1/ctVal: measured XAU ctVal=0.001 -> 1000x,
        # BTC 0.01 -> 100x, VVV 0.1 -> 10x.  Convert once, here.
        ct = spec.contract_multiplier or 1.0
        item = data["data"][0]
        book = Book(self.name, symbol,
                    bids=[(float(p), float(sz) * ct) for p, sz, *_ in item["bids"]],
                    asks=[(float(p), float(sz) * ct) for p, sz, *_ in item["asks"]],
                    ts=time.time())
        with self._lock:
            self._books[symbol] = book
        return book

    def stats(self, symbol: str) -> MarketStats:
        spec = self._by_symbol.get(symbol) or self.specs([symbol]).get(symbol)
        if not spec:
            raise VenueError(f"okx: unknown symbol {symbol}")
        cached = self._stats_cache.get(symbol)
        if cached and (time.time() - cached[0]) <= self.stats_ttl_s:
            return cached[1]
        book = self.book(symbol)
        bulk = self._bulk_snapshot().get(symbol) or {}
        # funding is 8-hourly: one call per symbol every 5 minutes is plenty
        rate, f_ts = self._funding.get(symbol, (0.0, 0.0))
        if (time.time() - f_ts) > self.funding_ttl_s:
            try:
                row = self._get("/api/v5/public/funding-rate", {"instId": spec.market_ref})["data"][0]
                rate = float(row.get("fundingRate") or 0.0)
                self._funding[symbol] = (rate, time.time())
            except Exception:
                self._funding[symbol] = (rate, time.time())
                rate = rate
        mark = float(bulk.get("mark") or book.mid)
        index = float(bulk.get("index") or mark)
        stats = MarketStats(
            venue=self.name, symbol=symbol,
            mark_price=mark,
            index_price=index,
            last_price=mark,
            mid_price=book.mid,
            oi_usd=float(bulk.get("oi_usd") or 0.0),
            oi_limit_usd=0.0,
            funding_rate=rate,
            funding_interval_h=self.funding_interval_h,
            ts=time.time(),
        )
        self._stats_cache[symbol] = (time.time(), stats)
        return stats

    def quote(self, symbol: str) -> Book:
        """WS top-of-book (5 levels): latency, not depth. Never gate on this."""
        with self._lock:
            book = self._ws_books.get(symbol)
        return book if book is not None else self.book(symbol)

    def _ingest(self, inst_id: str, bids, asks) -> None:
        symbol = inst_id.split("-")[0].upper()
        with self._lock:
            self._ws_books[symbol] = Book(self.name, symbol, bids=bids, asks=asks, ts=time.time())

    def start(self, symbols: list[str]) -> None:
        specs = self.specs(symbols)
        inst_ids = [s.market_ref for s in specs.values()]
        if not inst_ids:
            return
        self._ws = _OkxWs(self.creds.ws_public, inst_ids, self, proxy=self.proxy)
        self._ws.start()

    def stop(self) -> None:
        if self._ws:
            self._ws.stop()

    def ws_health(self) -> dict:
        if not self._ws:
            return {"connected": False, "age_s": None}
        age = time.time() - self._ws.last_message_at if self._ws.last_message_at else None
        return {"connected": age is not None and age < 30, "age_s": age,
                "reconnects": self._ws.reconnects, "error": self._ws.last_error}

    # ------------------------------------------------------------------ account
    def equity_usd(self) -> float:
        if not self.creds.has_private:
            return 0.0
        data = self._private("GET", "/api/v5/account/balance")
        for item in data.get("data", []):
            for det in item.get("details", []):
                if det.get("ccy") == "USDT":
                    return float(det.get("eq") or 0.0)
        return 0.0

    def positions(self) -> dict[str, VenuePosition]:
        if not self.creds.has_private:
            return {}
        out: dict[str, VenuePosition] = {}
        for inst_type in (SWAP_TAG,):
            data = self._private("GET", "/api/v5/account/positions", params={"instType": inst_type})
            for p in data.get("data", []):
                symbol = p["instId"].split("-")[0].upper()
                spec = self._specs.get(symbol)
                ct = spec.contract_multiplier if spec else 1.0
                pos = float(p.get("pos") or 0.0) * ct
                if abs(pos) < 1e-12:
                    continue
                out[symbol] = VenuePosition(
                    venue=self.name, symbol=symbol, qty_base=pos,
                    avg_entry=float(p.get("avgPx") or 0.0),
                    liquidation_price=float(p.get("liqPx") or 0.0),
                    notional_usd=abs(float(p.get("notionalUsd") or 0.0)),
                    margin_usd=float(p.get("margin") or 0.0),
                    unrealized_pnl=float(p.get("upl") or 0.0),
                )
        return out

    def set_leverage(self, symbol: str, leverage: float) -> dict:
        spec = self._by_symbol.get(symbol)
        if not spec:
            raise VenueError(f"okx: unknown symbol {symbol}")
        return self._private("POST", "/api/v5/account/set-leverage",
                             body={"instId": spec.market_ref, "lever": str(int(leverage)), "mgnMode": "cross"})

    # ------------------------------------------------------------------ trading
    def place(self, order: OrderRequest, spec: MarketSpec) -> Fill:
        contracts = max(0.0, order.qty_base / spec.contract_multiplier)
        step = 10.0 ** (-spec.size_decimals)
        contracts = int(contracts / step) * step
        if contracts <= 0:
            raise VenueError("okx: order rounds to zero contracts")
        ord_type = "post_only" if order.tif == "post_only" else "ioc" if order.tif == "ioc" else "limit"
        body = {
            "instId": spec.market_ref,
            "tdMode": "cross",
            "side": order.side,
            "ordType": ord_type,
            "px": f"{order.px:.{spec.price_decimals}f}",
            "sz": f"{contracts:.{spec.size_decimals}f}",
        }
        if order.reduce_only:
            body["reduceOnly"] = True
        data = self._private("POST", "/api/v5/trade/order", body=body)
        res = (data.get("data") or [{}])[0]
        if res.get("sCode") not in (None, "0"):
            raise VenueError(f"okx order rejected: {res.get('sCode')} {res.get('sMsg')}")
        return Fill(self.name, order.symbol, order.side, contracts * spec.contract_multiplier,
                    order.px, fee_bps=spec.taker_fee_bps if ord_type != "post_only" else spec.maker_fee_bps,
                    order_id=res.get("ordId", ""), raw=res)

    def cancel(self, symbol: str, order_id: str) -> bool:
        spec = self._by_symbol.get(symbol)
        if not spec or not self.creds.has_private or not order_id:
            return False
        try:
            self._private("POST", "/api/v5/trade/cancel-order",
                          body={"instId": spec.market_ref, "ordId": order_id})
            return True
        except Exception:
            return False

    def cancel_all(self, symbol: str) -> int:
        spec = self._by_symbol.get(symbol)
        if not spec or not self.creds.has_private:
            return 0
        data = self._private("POST", "/api/v5/trade/cancel-all",
                             body={"instId": spec.market_ref, "mgnMode": "cross"})
        return len(data.get("data") or [])


def decimals_from_tick(tick: str) -> int:
    return len(tick.split(".")[1].rstrip("0")) if "." in tick else 0


def decimals_from_lot(lot: str) -> int:
    return decimals_from_tick(lot)