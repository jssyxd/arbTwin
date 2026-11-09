"""Queue-reactive fill model for resting post-only quotes (validation T2).

Everything here is measured, not asserted.  Three things come out of a capture:

1. ``PriceThroughModel`` -- the *conservative* baseline, and the exact rule
   ``arbtwin.wavetrader.RestingQuote.crossed`` already uses in dry-run: a resting
   buy at ``p`` is filled only once the best ask trades strictly *below* ``p``
   (and a sell only once the best bid trades strictly above).  No queue position
   is invented.  Its ``p_fill`` is a lower bound.
2. ``QueueDepletionModel`` -- analytic.  We join the *back* of the visible queue at
   ``p``; the queue ahead of us shrinks one-for-one with (a) taker flow executing
   against resting orders at ``p`` and (b) cancellations of orders that were ahead
   of us.  Under FIFO those are the only two channels, and both are directly
   observable: Lighter's REST depth is *order-level* (``order_index`` is monotone
   in placement order), so "size ahead of me" and "which order vanished" are facts.
3. ``QueueMonteCarloModel`` -- same rates, but the distance-to-mid is not frozen:
   the mid path is bootstrapped (or OU-fitted) from the capture, depletion and
   through-hazards are re-evaluated at the *current* distance each step.  This is
   the variant that answers "what happens when the dislocation runs away".

Calibration is per (symbol, side, distance bucket) and every number carries its
sample size, because a bucket with three spells is an anecdote, not a rate.

Provenance discipline: ``Capture.source`` is ``"captured"`` or ``"synthetic"`` and
is copied into every result dict.  A synthetic number must never be quoted as a
market measurement.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# capture loading
# --------------------------------------------------------------------------- #

DEFAULT_BUCKETS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 1e9)


@dataclass
class Capture:
    """A recorded (or synthesised) market sample.

    Arrays, not DataFrames, on the hot path: the Monte Carlo re-reads these
    thousands of times and pandas indexing would dominate the runtime.
    """

    source: str                                  # "captured" | "synthetic"
    symbols: list[str] = field(default_factory=list)
    # WS order_book: parallel arrays, one row per message
    book_ts: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_sym: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    book_bid: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_ask: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_mid: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_bid_sz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_ask_sz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_spread_bps: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_latency_ms: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_venue_ts_ms: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_resync_age_s: np.ndarray = field(default_factory=lambda: np.zeros(0))
    book_offset_gap: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    # WS trades
    trade_ts: np.ndarray = field(default_factory=lambda: np.zeros(0))
    trade_sym: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    trade_px: np.ndarray = field(default_factory=lambda: np.zeros(0))
    trade_sz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    trade_maker_ask: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    # REST order-level depth (already tidied by the recorder's parquet step)
    order_ts: np.ndarray = field(default_factory=lambda: np.zeros(0))
    order_sym: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    order_index: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    order_px: np.ndarray = field(default_factory=lambda: np.zeros(0))
    order_remaining: np.ndarray = field(default_factory=lambda: np.zeros(0))
    order_initial: np.ndarray = field(default_factory=lambda: np.zeros(0))
    order_side: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    order_mid: np.ndarray = field(default_factory=lambda: np.zeros(0))
    meta: dict = field(default_factory=dict)
    symbol_ids: dict[str, int] = field(default_factory=dict)

    # -- convenience ---------------------------------------------------------
    def n_books(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self.book_ts)
        return int((self.book_sym == self.symbol_ids[symbol]).sum())

    def duration_s(self) -> float:
        return float(self.book_ts[-1] - self.book_ts[0]) if len(self.book_ts) > 1 else 0.0

    def books(self, symbol: str) -> dict[str, np.ndarray]:
        sid = self.symbol_ids[symbol]
        m = self.book_sym == sid
        return {"ts": self.book_ts[m], "bid": self.book_bid[m], "ask": self.book_ask[m],
                "mid": self.book_mid[m], "bid_sz": self.book_bid_sz[m],
                "ask_sz": self.book_ask_sz[m], "spread_bps": self.book_spread_bps[m],
                "latency": self.book_latency_ms[m], "resync_age_s": self.book_resync_age_s[m],
                "n_levels": self.book_offset_gap[m]}

    def trades(self, symbol: str) -> dict[str, np.ndarray]:
        sid = self.symbol_ids[symbol]
        m = self.trade_sym == sid
        return {"ts": self.trade_ts[m], "px": self.trade_px[m], "sz": self.trade_sz[m],
                "maker_ask": self.trade_maker_ask[m]}

    def orders(self, symbol: str) -> dict[str, np.ndarray]:
        sid = self.symbol_ids[symbol]
        m = self.order_sym == sid
        return {"ts": self.order_ts[m], "index": self.order_index[m], "px": self.order_px[m],
                "remaining": self.order_remaining[m], "initial": self.order_initial[m],
                "side": self.order_side[m], "mid": self.order_mid[m]}

    def describe(self) -> dict:
        out = {"source": self.source, "duration_s": round(self.duration_s(), 1),
               "n_book_msgs": int(len(self.book_ts)), "n_trades": int(len(self.trade_ts)),
               "n_order_rows": int(len(self.order_ts)), "symbols": {}}
        for sym in self.symbols:
            b = self.books(sym)
            t = self.trades(sym)
            dt = np.diff(b["ts"]) if len(b["ts"]) > 1 else np.zeros(0)
            out["symbols"][sym] = {
                "book_msgs": int(len(b["ts"])),
                "book_msg_rate_per_s": round(float(len(b["ts"]) / max(1e-9, self.duration_s())), 2),
                "median_dt_ms": round(float(np.median(dt)) * 1000, 1) if len(dt) else None,
                "mean_latency_ms": round(float(np.nanmean(b["latency"])), 1)
                if len(b["ts"]) and np.isfinite(b["latency"]).any() else None,
                "median_spread_bps": round(float(np.median(b["spread_bps"])), 3) if len(b["ts"]) else None,
                "median_skew_corrected_latency_ms": round(float(np.nanmedian(b["latency"])), 1)
                if len(b["ts"]) else None,
                "median_resync_age_s": round(float(np.median(b["resync_age_s"])), 2) if len(b["ts"]) else None,
                "median_maintained_levels": int(np.median(b["n_levels"])) if len(b["ts"]) else None,
                "rebuild": self.meta.get("book_rebuild", {}).get(sym),
                "trades": int(len(t["ts"])),
                "traded_base_per_s": round(float(t["sz"].sum() / max(1e-9, self.duration_s())), 5),
                "mid_bps_move_per_sqrt_min": round(float(np.std(np.diff(b["mid"]) / b["mid"][1:] * 1e4)
                                                         / math.sqrt(max(1e-9, np.median(dt) * 60))), 3)
                if len(dt) else None,
            }
        return out


def _read_jsonl(path: str) -> Iterable[dict]:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:      # truncated tail on a hard kill
                    continue


def rebuild_top_of_book(ws_rows: list[dict], rest_rows: list[dict]) -> tuple[list[dict], dict]:
    """Maintain a local L2 book from WS *deltas*, resynced by REST full snapshots.

    Lighter's WS ``order_book`` channel is incremental (measured 2026-11-10: the
    first message per market carries the full ladder -- ``n_bid_levels`` 684 for
    ETH -- later messages carry only changed levels, and a level with ``size=0``
    is a deletion).  Treating those deltas as snapshots is what produces negative
    spreads and one-sided books, so the book is rebuilt here instead:

    * the first WS message seeds the state (it is a snapshot);
    * every later WS message is applied as a delta (``size=0`` -> remove level);
    * every REST ``orderBookOrders`` poll *resets* the state from its full 250-row
      ladder, so drift cannot accumulate for longer than the poll interval;
    * before each reset the maintained touch is compared with the poll's own
      touch -- that mismatch count is the integrity evidence for the rebuild.

    Rows whose maintained book is one-sided or crossed are dropped and counted.
    """
    events = [(r["recv_ts"], 0, r) for r in ws_rows] + [(r["recv_ts"], 1, r) for r in rest_rows]
    events.sort(key=lambda e: (e[0], e[1]))          # a poll at the same ts wins second
    state_b: dict[float, float] = {}
    state_a: dict[float, float] = {}
    seeded = False
    last_resync = 0.0
    out: list[dict] = []
    stats = {"ws_msgs": len(ws_rows), "rest_resyncs": 0, "touch_mismatch_before_resync": 0,
             "dropped_one_sided": 0, "dropped_crossed": 0, "emitted": 0, "snapshot_seeded": False,
             "max_resync_age_s": 0.0}
    for ts, kind, row in events:
        if kind == 1:                                # REST full ladder -> authoritative reset
            bid_lv = row.get("bid_levels") or []
            ask_lv = row.get("ask_levels") or []
            if bid_lv or ask_lv:
                if seeded and (row.get("bid") or 0) > 0 and (row.get("ask") or 0) > 0:
                    mb = max(state_b) if state_b else 0.0
                    ma = min(state_a) if state_a else 0.0
                    if abs(mb - float(row["bid"])) > 1e-9 or abs(ma - float(row["ask"])) > 1e-9:
                        stats["touch_mismatch_before_resync"] += 1
                state_b = {float(l[0]): float(l[1]) for l in bid_lv if float(l[1]) > 0}
                state_a = {float(l[0]): float(l[1]) for l in ask_lv if float(l[1]) > 0}
                seeded = True
                last_resync = ts
                stats["rest_resyncs"] += 1
        else:                                        # WS message
            db = row.get("delta_bids") or row.get("bids") or []
            da = row.get("delta_asks") or row.get("asks") or []
            is_snap = bool(row.get("is_snapshot")) or (not seeded and db and da)
            if is_snap:
                state_b, state_a = {}, {}
                seeded = True
                stats["snapshot_seeded"] = True
            for px, sz in db:
                px, sz = float(px), float(sz)
                if sz <= 0:
                    state_b.pop(px, None)
                else:
                    state_b[px] = sz
            for px, sz in da:
                px, sz = float(px), float(sz)
                if sz <= 0:
                    state_a.pop(px, None)
                else:
                    state_a[px] = sz
        if not seeded or not state_b or not state_a:
            stats["dropped_one_sided"] += 1
            continue
        bb = max(state_b)
        ba = min(state_a)
        if ba <= bb:
            stats["dropped_crossed"] += 1
            continue
        age = ts - last_resync if last_resync else 0.0
        stats["max_resync_age_s"] = max(stats["max_resync_age_s"], age)
        out.append({"ts": ts, "bid": bb, "ask": ba, "mid": (bb + ba) / 2.0,
                    "bid_sz": state_b[bb], "ask_sz": state_a[ba],
                    "spread_bps": (ba - bb) / ((bb + ba) / 2.0) * 1e4,
                    "venue_ts_ms": float(row.get("venue_ts_ms") or 0.0),
                    "n_levels": len(state_b) + len(state_a), "resync_age_s": age})
    stats["emitted"] = len(out)
    return out, stats


def load_capture(directory: str, source: str = "captured") -> Capture:
    """Load a recorder output directory (JSONL; Parquet is only a convenience copy)."""
    meta = {}
    meta_path = os.path.join(directory, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)

    ws_rows: dict[str, list[dict]] = {}
    rest_rows: dict[str, list[dict]] = {}
    trades, orders = [], []
    symbols: dict[str, int] = {}
    for row in _read_jsonl(os.path.join(directory, "book.jsonl")):
        sym = row["symbol"]
        symbols.setdefault(sym, len(symbols))
        ws_rows.setdefault(sym, []).append(row)
    for row in _read_jsonl(os.path.join(directory, "book_orders.jsonl")):
        sym = row["symbol"]
        if sym not in symbols:
            continue
        rest_rows.setdefault(sym, []).append(row)
        if row.get("orders_bps"):
            meta["orders_bps"] = float(row["orders_bps"])
        sid = symbols[sym]
        midpx = row.get("mid") or 0.0
        for oi, px, rem, init, side in row.get("orders") or []:
            orders.append((row["recv_ts"], sid, int(oi), float(px), float(rem), float(init),
                           int(side), float(midpx)))
    for row in _read_jsonl(os.path.join(directory, "trades.jsonl")):
        sym = row["symbol"]
        if sym not in symbols:                       # trades for an unrecorded market
            continue
        trades.append((row["recv_ts"], symbols[sym], row.get("px") or 0.0, row.get("sz") or 0.0,
                       bool(row.get("is_maker_ask")), float(row.get("venue_ts_ms") or 0.0)))

    cap = Capture(source=source, symbols=sorted(symbols, key=lambda s: symbols[s]),
                  symbol_ids=symbols, meta=meta)
    books, rebuild = [], {}
    for sym, rows in ws_rows.items():
        built, stats = rebuild_top_of_book(rows, rest_rows.get(sym, []))
        rebuild[sym] = stats
        sid = symbols[sym]
        for r in built:
            books.append((r["ts"], sid, r["bid"], r["ask"], r["mid"], r["bid_sz"], r["ask_sz"],
                          r["spread_bps"], r["venue_ts_ms"], r["resync_age_s"], r["n_levels"]))
    cap.meta["book_rebuild"] = rebuild
    if books:
        a = np.array(books, dtype=float)
        a = a[np.argsort(a[:, 0], kind="stable")]
        cap.book_ts, cap.book_sym = a[:, 0], a[:, 1].astype(int)
        cap.book_bid, cap.book_ask, cap.book_mid = a[:, 2], a[:, 3], a[:, 4]
        cap.book_bid_sz, cap.book_ask_sz, cap.book_spread_bps = a[:, 5], a[:, 6], a[:, 7]
        cap.book_venue_ts_ms, cap.book_resync_age_s = a[:, 8], a[:, 9]
        cap.book_offset_gap = a[:, 10].astype(int)       # reused: maintained level count
        # the venue clock runs ahead of this host by a stable offset (measured
        # ~+0.79 s), so raw recv-venue is *skew*, not latency.  Report the
        # skew-corrected residual, which is the transport jitter that matters.
        skew = a[:, 0] * 1000 - a[:, 8]
        ok = skew != 0
        cap.meta["clock_skew_ms_median"] = float(np.median(skew[ok])) if ok.any() else None
        cap.book_latency_ms = (skew - cap.meta["clock_skew_ms_median"]) if ok.any() else skew
    if trades:
        t = np.array(trades, dtype=float)
        t = t[np.argsort(t[:, 0], kind="stable")]
        cap.trade_ts, cap.trade_sym = t[:, 0], t[:, 1].astype(int)
        cap.trade_px, cap.trade_sz = t[:, 2], t[:, 3]
        cap.trade_maker_ask = t[:, 4].astype(bool)
        cap.meta["trade_venue_ts_skew_ms_median"] = float(
            np.median(t[:, 0][t[:, 5] > 0] * 1000 - t[:, 5][t[:, 5] > 0])) if (t[:, 5] > 0).any() else None
    if orders:
        o = np.array(orders, dtype=float)
        o = o[np.argsort(o[:, 0], kind="stable")]
        cap.order_ts, cap.order_sym = o[:, 0], o[:, 1].astype(int)
        cap.order_index = o[:, 2].astype(np.int64)
        cap.order_px, cap.order_remaining, cap.order_initial = o[:, 3], o[:, 4], o[:, 5]
        cap.order_side, cap.order_mid = o[:, 6].astype(int), o[:, 7]
    return cap


# --------------------------------------------------------------------------- #
# bucketing helpers
# --------------------------------------------------------------------------- #

def bucket_of(distance_bps: float, edges: Sequence[float] = DEFAULT_BUCKETS) -> int:
    for i, e in enumerate(edges):
        if distance_bps < e:
            return i
    return len(edges) - 1


def bucket_label(i: int, edges: Sequence[float] = DEFAULT_BUCKETS) -> str:
    lo = edges[i - 1] if i > 0 else 0.0
    hi = edges[i]
    return f"[{lo:g},{hi:g})bps" if hi < 1e8 else f"[{lo:g},inf)bps"


def distance_bps(px: float, mid: float, side: str) -> float:
    """How far *into our own side* the price is.  >=0 for a resting maker quote."""
    if mid <= 0:
        return float("nan")
    d = (mid - px) / mid * 1e4 if side == "buy" else (px - mid) / mid * 1e4
    return float(d)


# --------------------------------------------------------------------------- #
# measurement: level spells, trade consumption, cancel/placement flow
# --------------------------------------------------------------------------- #

@dataclass
class LevelSpell:
    symbol: str
    side: str                 # "buy" = resting bid, "sell" = resting ask
    px: float
    t0: float
    t1: float
    size0: float
    size_end: float
    traded_base: float
    n_trades: int
    d0_bps: float             # distance from mid at t0 (>=0 = inside our own side)
    d_end_bps: float


def touch_spells(cap: Capture, symbol: str, side: str) -> list[LevelSpell]:
    """Contiguous lifetimes of the *touch* level, from WS snapshots.

    A spell runs over the maximal set of consecutive snapshots in which the best
    price on our side is unchanged.  Inside a spell, prints at that price whose
    maker sat on our side are executions against resting orders like ours, so
    ``traded_base / duration`` is a direct execution-rate measurement.

    Scope note: ``Capture`` loads the touch (bid/ask + sizes) but not the trimmed
    ladder, so spells are reconstructed where a post-only quote actually joins --
    the touch.  Deeper levels are covered by the order-level REST polls in
    ``order_flow``, which is the better source anyway (it has order identity).
    A spell cannot distinguish "level consumed and instantly re-posted at the same
    price" from "level untouched"; that biases the lifetime up slightly and is
    why the primary execution rate comes from the tape, not from spells.
    """
    b = cap.books(symbol)
    ts, mid = b["ts"], b["mid"]
    px = b["bid"] if side == "buy" else b["ask"]
    sz = b["bid_sz"] if side == "buy" else b["ask_sz"]
    t = cap.trades(symbol)
    out: list[LevelSpell] = []
    n = len(ts)
    i = 0
    while i < n:
        if px[i] <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and abs(px[j + 1] - px[i]) < 1e-12:
            j += 1
        t0, t1 = float(ts[i]), float(ts[j])
        traded, ntr = 0.0, 0
        if len(t["ts"]) and t1 > t0:
            m = ((t["ts"] > t0) & (t["ts"] <= t1) & (np.abs(t["px"] - px[i]) < 1e-9))
            m &= t["maker_ask"] if side == "sell" else ~t["maker_ask"]
            traded, ntr = float(t["sz"][m].sum()), int(m.sum())
        out.append(LevelSpell(symbol=symbol, side=side, px=float(px[i]), t0=t0, t1=t1,
                              size0=float(sz[i]), size_end=float(sz[j]), traded_base=traded,
                              n_trades=ntr, d0_bps=distance_bps(float(px[i]), float(mid[i]), side),
                              d_end_bps=distance_bps(float(px[j]), float(mid[j]), side)))
        i = j + 1
    return out


def _at(cap: Capture, symbol: str, ts: float, key: str) -> float:
    b = cap.books(symbol)
    if not len(b["ts"]):
        return 0.0
    i = int(np.searchsorted(b["ts"], ts))
    i = min(max(i, 0), len(b["ts"]) - 1)
    return float(b[key][i])


@dataclass
class OrderFlow:
    """Cancel / placement / execution flow measured from *order-level* REST polls.

    Identity is ``order_index`` (venue transaction index, monotone in placement
    order).  An order present in poll k and absent in poll k+1 either executed or
    was cancelled; the trade tape in the same interval says which.  Every quantity
    is accumulated **per distance bucket**, because a rate averaged over the whole
    +/-60 bps window and then re-split by queue weight would make the cancel drain
    hazard identical at every distance -- which is not a measurement, it is an
    artefact of the averaging.
    """

    symbol: str
    side: str
    n_polls: int
    poll_span_s: float
    seen_orders: int
    vanished_orders: int
    vanished_base: float
    vanished_base_traded: float          # explained by prints in the interval
    vanished_base_cancel: float          # residual -> cancellation
    new_orders: int
    new_base: float
    edges: Sequence[float] = DEFAULT_BUCKETS
    # per-bucket base units over the poll window (divide by poll_span_s for rates)
    cancel_base_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    traded_base_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    tape_base_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    place_base_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    queue_base_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    queue_p90_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    order_size_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    n_levels_by_bucket: np.ndarray = field(default_factory=lambda: np.zeros(0))
    orders_bps_window: float = 60.0
    queue_base_per_level: list[float] = field(default_factory=list)   # size at each price
    order_size_base: list[float] = field(default_factory=list)        # per resting order
    d_bps: list[float] = field(default_factory=list)                  # distance of each price


def order_flow(cap: Capture, symbol: str, side: str, min_polls: int = 2,
               edges: Sequence[float] = DEFAULT_BUCKETS) -> Optional[OrderFlow]:
    o = cap.orders(symbol)
    if not len(o["ts"]):
        return None
    want = 1 if side == "sell" else -1
    m = o["side"] == want
    if not m.any():
        return None
    ts, idx, px, rem, mid = (o["ts"][m], o["index"][m], o["px"][m], o["remaining"][m], o["mid"][m])
    polls = np.unique(ts)
    if len(polls) < min_polls:
        return None
    nb = len(edges)
    t = cap.trades(symbol)
    flow = OrderFlow(symbol=symbol, side=side, n_polls=int(len(polls)),
                     poll_span_s=float(polls[-1] - polls[0]), seen_orders=0, vanished_orders=0,
                     vanished_base=0.0, vanished_base_traded=0.0, vanished_base_cancel=0.0,
                     new_orders=0, new_base=0, edges=edges,
                     cancel_base_by_bucket=np.zeros(nb), traded_base_by_bucket=np.zeros(nb),
                     tape_base_by_bucket=np.zeros(nb), place_base_by_bucket=np.zeros(nb),
                     queue_base_by_bucket=np.full(nb, np.nan),
                     queue_p90_by_bucket=np.full(nb, np.nan),
                     order_size_by_bucket=np.full(nb, np.nan),
                     n_levels_by_bucket=np.zeros(nb),
                     orders_bps_window=float(cap.meta.get("orders_bps", 60.0)))
    win = flow.orders_bps_window
    level_d: dict[int, list[float]] = {i: [] for i in range(nb)}
    level_q: dict[int, list[float]] = {i: [] for i in range(nb)}
    order_sz: dict[int, list[float]] = {i: [] for i in range(nb)}
    prev: dict[int, tuple[float, float, float]] = {}      # index -> (px, remaining, mid)
    for k, p_ts in enumerate(polls):
        sel = ts == p_ts
        cur_idx, cur_px, cur_rem = idx[sel], px[sel], rem[sel]
        cur_mid = float(mid[sel][0]) if sel.any() else 0.0
        cur: dict[int, tuple[float, float, float]] = {}
        agg: dict[float, float] = {}
        for j in range(len(cur_idx)):
            p_px, p_rem = float(cur_px[j]), float(cur_rem[j])
            cur[int(cur_idx[j])] = (p_px, p_rem, cur_mid)
            agg[p_px] = agg.get(p_px, 0.0) + p_rem
            flow.order_size_base.append(p_rem)
            if cur_mid > 0:
                d = distance_bps(p_px, cur_mid, side)
                if np.isfinite(d) and abs(d) <= win:
                    i = bucket_of(max(d, 0.0), edges)
                    order_sz[i].append(p_rem)
        for p_px, sz in agg.items():
            flow.queue_base_per_level.append(sz)
            if cur_mid <= 0:
                continue
            d = distance_bps(p_px, cur_mid, side)
            if not np.isfinite(d) or abs(d) > win:
                continue
            i = bucket_of(max(d, 0.0), edges)
            level_q[i].append(sz)
            level_d[i].append(d)
            flow.n_levels_by_bucket[i] += 1
        if k == 0:
            flow.seen_orders = len(cur)
            prev = cur
            continue
        new = [i for i in cur if i not in prev]
        gone = [i for i in prev if i not in cur]
        flow.new_orders += len(new)
        flow.new_base += sum(cur[i][1] for i in new)
        flow.seen_orders += len(cur)
        for i in new:
            p_px, p_rem, p_mid = cur[i]
            d = distance_bps(p_px, p_mid, side)
            if np.isfinite(d) and abs(d) <= win:
                flow.place_base_by_bucket[bucket_of(max(d, 0.0), edges)] += p_rem
        if gone:
            lo, hi = float(polls[k - 1]), float(p_ts)
            for i in gone:
                p_px, p_rem, p_mid = prev[i]
                traded = 0.0
                if len(t["ts"]):
                    tm = (t["ts"] > lo) & (t["ts"] <= hi) & (np.abs(t["px"] - p_px) < 1e-9)
                    tm &= t["maker_ask"] if side == "sell" else ~t["maker_ask"]
                    traded = float(t["sz"][tm].sum())
                traded = min(traded, p_rem)
                flow.vanished_orders += 1
                flow.vanished_base += p_rem
                flow.vanished_base_traded += traded
                flow.vanished_base_cancel += max(0.0, p_rem - traded)
                d = distance_bps(p_px, p_mid, side)
                if np.isfinite(d) and abs(d) <= win:
                    b = bucket_of(max(d, 0.0), edges)
                    flow.traded_base_by_bucket[b] += traded
                    flow.cancel_base_by_bucket[b] += max(0.0, p_rem - traded)
        prev = cur
    for i in range(nb):
        if level_q[i]:
            flow.queue_base_by_bucket[i] = float(np.median(level_q[i]))
            flow.queue_p90_by_bucket[i] = float(np.quantile(level_q[i], 0.9))
            flow.d_bps.extend(level_d[i])
        if order_sz[i]:
            flow.order_size_by_bucket[i] = float(np.median(order_sz[i]))

    # tape-side execution flow per bucket, over the same window as the polls:
    # prints whose maker was on our side consume queue exactly like ours would.
    if len(t["ts"]):
        in_win = (t["ts"] >= polls[0]) & (t["ts"] <= polls[-1])
        maker_ours = t["maker_ask"] if side == "sell" else ~t["maker_ask"]
        sel = in_win & maker_ours
        if sel.any():
            t_ts, t_px, t_sz = t["ts"][sel], t["px"][sel], t["sz"][sel]
            j = np.clip(np.searchsorted(polls, t_ts, side="right") - 1, 0, len(polls) - 1)
            poll_mid = np.array([float(mid[ts == polls[k]][0]) if (ts == polls[k]).any() else np.nan
                                 for k in range(len(polls))])
            ref = poll_mid[j]
            ok = np.isfinite(ref) & (ref > 0)
            d = np.where(ok, distance_bps_arr(t_px, np.where(ok, ref, 1.0), side), np.nan)
            keep = ok & np.isfinite(d) & (np.abs(d) <= win)
            for b in range(nb):
                mb = keep & (d >= (edges[b - 1] if b else -1e9)) & (d < edges[b])
                if mb.any():
                    flow.tape_base_by_bucket[b] = float(t_sz[mb].sum())
    return flow


@dataclass
class Calibration:
    """Per-bucket rates.  ``n`` is the sample size behind each rate."""

    symbol: str
    side: str
    source: str
    buckets: list[str]
    n_spells: np.ndarray
    spell_seconds: np.ndarray
    trade_rate_base_per_s: np.ndarray      # mu_trade(d): execution against resting orders
    cancel_rate_base_per_s: np.ndarray     # mu_cancel(d): size withdrawn ahead of us
    place_rate_base_per_s: np.ndarray      # size added behind us (does not help us fill)
    through_hazard_per_s: np.ndarray       # pi(d): opposing best trades strictly through
    queue_base: np.ndarray                 # Q0(d): median visible size ahead at that price
    queue_base_p90: np.ndarray
    order_size_base: np.ndarray            # median size of a single resting order
    level_lifetime_s: np.ndarray           # median spell duration
    queue_drain_rate_per_s: np.ndarray     # (mu_trade + mu_cancel) / Q0 -> fill hazard
    spell_d_bps: np.ndarray                # median distance of the bucket's spells
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def r(a):
            return [None if (x is None or (isinstance(x, float) and not np.isfinite(x))) else round(float(x), 6)
                    for x in np.atleast_1d(a)]
        return {"symbol": self.symbol, "side": self.side, "source": self.source,
                "buckets": list(self.buckets), "n_spells": r(self.n_spells),
                "spell_seconds": r(self.spell_seconds),
                "trade_rate_base_per_s": r(self.trade_rate_base_per_s),
                "cancel_rate_base_per_s": r(self.cancel_rate_base_per_s),
                "place_rate_base_per_s": r(self.place_rate_base_per_s),
                "through_hazard_per_s": r(self.through_hazard_per_s),
                "queue_base": r(self.queue_base), "queue_base_p90": r(self.queue_base_p90),
                "order_size_base": r(self.order_size_base),
                "level_lifetime_s": r(self.level_lifetime_s),
                "queue_drain_rate_per_s": r(self.queue_drain_rate_per_s),
                "spell_d_bps": r(self.spell_d_bps), "notes": list(self.notes)}


def through_hazard(cap: Capture, symbol: str, side: str,
                   thresholds_bps: Sequence[float], min_move_bps: float = 0.0) -> np.ndarray:
    """Rate at which the opposing best price moves *through* a level at distance d.

    For a resting buy at distance ``d`` bps below the mid, a price-through fill
    needs the best ask to fall by ``d + half_spread``.  We measure the empirical
    hazard of "best ask falls by >= delta" (or "best bid rises by >= delta" for a
    sell) per second of recording -- a downward-jump intensity, not an assumption.

    ``min_move_bps`` must be at least one tick: with a threshold of 0 the test
    ``move >= 0`` is true for every message (moves are floored at 0) and the
    "hazard" degenerates into the message rate.
    """
    b = cap.books(symbol)
    dur = cap.duration_s()
    out = np.zeros(len(thresholds_bps))
    if dur <= 0 or len(b["ts"]) < 3:
        return out
    px = b["ask"] if side == "buy" else b["bid"]
    ref = b["mid"]
    keep = (px > 0) & (ref > 0)
    px, ref = px[keep], ref[keep]
    if len(px) < 3:
        return out
    prev, cur, r = px[:-1], px[1:], ref[:-1]
    if side == "buy":
        move = (prev - cur) / r * 1e4            # ask falling towards our bid
    else:
        move = (cur - prev) / r * 1e4            # bid rising towards our ask
    move = np.maximum(move, 0.0)
    floor = max(min_move_bps, 1e-9)
    for i, thr in enumerate(thresholds_bps):
        out[i] = float((move >= max(thr, floor)).sum()) / dur
    return out


def tick_bps(cap: Capture, symbol: str) -> float:
    """Smallest observed price step, in bps of the median price (the venue tick)."""
    b = cap.books(symbol)
    if len(b["ts"]) < 2:
        return float("nan")
    px = b["bid"][b["bid"] > 0]
    if len(px) < 2:
        return float("nan")
    steps = np.diff(np.unique(np.round(px, 12)))
    steps = steps[steps > 0]
    med = float(np.median(b["mid"][b["mid"] > 0])) if (b["mid"] > 0).any() else float(np.median(px))
    return float(steps.min() / med * 1e4) if len(steps) and med > 0 else float("nan")


def calibrate(cap: Capture, symbol: str, side: str,
              edges: Sequence[float] = DEFAULT_BUCKETS) -> Calibration:
    """Measure every rate the fill models consume, per distance bucket."""
    nb = len(edges)
    lab = [bucket_label(i, edges) for i in range(nb)]
    n_sp = np.zeros(nb)
    sp_s = np.zeros(nb)
    mu_tr = np.zeros(nb)
    mu_ca = np.zeros(nb)
    mu_pl = np.zeros(nb)
    q0 = np.full(nb, np.nan)
    q90 = np.full(nb, np.nan)
    osz = np.full(nb, np.nan)
    life = np.full(nb, np.nan)
    dsp = np.full(nb, np.nan)
    notes: list[str] = []

    # 1) through hazard: needs the half-spread, because crossing our price means
    #    the *opposing* best has to travel d + half_spread, not just d.
    b = cap.books(symbol)
    half_spread = float(np.median(b["spread_bps"][b["spread_bps"] > 0]) / 2.0) \
        if (b["spread_bps"] > 0).any() else 0.0
    tick = tick_bps(cap, symbol)
    tick = tick if np.isfinite(tick) else 0.0
    thr = [edges[i - 1] + half_spread if i > 0 else half_spread for i in range(nb)]
    hz = through_hazard(cap, symbol, side, thr, min_move_bps=max(tick, 1e-9))

    # 2) touch level spells -> level lifetime, plus an independent read of the
    #    execution rate at the touch (cross-check on the tape number in step 3).
    spells = touch_spells(cap, symbol, side)
    life_list: dict[int, list[float]] = {i: [] for i in range(nb)}
    spell_trade_base = np.zeros(nb)
    for sp in spells:
        if not np.isfinite(sp.d0_bps):
            continue
        i = bucket_of(max(sp.d0_bps, 0.0), edges)
        dur = max(1e-9, sp.t1 - sp.t0)
        n_sp[i] += 1
        sp_s[i] += dur
        spell_trade_base[i] += sp.traded_base
        life_list[i].append(dur)
        if not np.isfinite(dsp[i]):
            dsp[i] = sp.d0_bps
    life_arr = np.array([float(np.median(v)) if v else np.nan for v in
                         (life_list[i] for i in range(nb))])

    # 3) order-level REST flow -> queue size, cancels, placements, tape execution
    flow = order_flow(cap, symbol, side, edges=edges)
    if flow is None:
        notes.append("no order-level REST rows: cancel/placement rates unavailable, "
                     "queue size taken from WS top-of-book, execution from touch spells")
        q_ws = b["bid_sz"] if side == "buy" else b["ask_sz"]
        d_ws = distance_bps_arr(b["bid"] if side == "buy" else b["ask"], b["mid"], side)
        for i in range(nb):
            m = np.isfinite(d_ws) & (d_ws >= (edges[i - 1] if i else 0.0)) & (d_ws < edges[i])
            if m.any():
                q0[i] = float(np.median(q_ws[m]))
                q90[i] = float(np.quantile(q_ws[m], 0.9))
        with np.errstate(divide="ignore", invalid="ignore"):
            mu_tr = np.where(sp_s > 0, spell_trade_base / np.maximum(sp_s, 1e-9), np.nan)
    else:
        span = max(1e-9, flow.poll_span_s)
        q0 = np.array(flow.queue_base_by_bucket, dtype=float)
        q90 = np.array(flow.queue_p90_by_bucket, dtype=float)
        osz = np.array(flow.order_size_by_bucket, dtype=float)
        mu_ca = np.array(flow.cancel_base_by_bucket, dtype=float) / span
        mu_pl = np.array(flow.place_base_by_bucket, dtype=float) / span
        # two independent reads of execution flow against resting orders:
        #  * tape   : every print whose maker sat on our side (complete, but only
        #             for prices inside the polled +/-window)
        #  * vanish : size that disappeared from a polled order and is explained by
        #             a print (a lower bound -- polls miss orders born and killed
        #             between two polls)
        mu_tr_tape = np.array(flow.tape_base_by_bucket, dtype=float) / span
        mu_tr_vanish = np.array(flow.traded_base_by_bucket, dtype=float) / span
        # A bucket with observed levels and zero prints is a *measured zero*, not a
        # missing value: that distance simply saw no execution against resting
        # orders during the window.  Buckets with no observed levels stay NaN.
        observed = np.array(flow.n_levels_by_bucket, dtype=float) > 0
        mu_tr = np.where(observed, np.maximum(mu_tr_tape, mu_tr_vanish), np.nan)
        if np.isfinite(spell_trade_base).any() and sp_s.any():
            spell_rate = np.where(sp_s > 0, spell_trade_base / np.maximum(sp_s, 1e-9), np.nan)
            notes.append(f"touch cross-check: tape mu={float(np.nanmax(mu_tr_tape)):.4g} vs "
                         f"spell mu={float(np.nanmax(spell_rate)):.4g} base/s")
        notes.append(f"order-level polls={flow.n_polls} span={span:.0f}s window=+/-{flow.orders_bps_window:g}bps "
                     f"vanished={flow.vanished_orders} (traded {flow.vanished_base_traded:.3f} / "
                     f"cancelled {flow.vanished_base_cancel:.3f} base) new={flow.new_orders}")

    drain = (np.nan_to_num(mu_tr) + np.nan_to_num(mu_ca)) / np.where(np.isfinite(q0) & (q0 > 0), q0, np.nan)
    return Calibration(symbol=symbol, side=side, source=cap.source, buckets=lab,
                       n_spells=n_sp, spell_seconds=sp_s, trade_rate_base_per_s=mu_tr,
                       cancel_rate_base_per_s=mu_ca, place_rate_base_per_s=mu_pl,
                       through_hazard_per_s=hz, queue_base=q0, queue_base_p90=q90,
                       order_size_base=osz, level_lifetime_s=life_arr,
                       queue_drain_rate_per_s=drain, spell_d_bps=dsp, notes=notes)


def distance_bps_arr(px: np.ndarray, mid: np.ndarray, side: str) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(mid > 0, (mid - px) / np.where(mid > 0, mid, 1.0) * 1e4, np.nan)
    return d if side == "buy" else -d


# --------------------------------------------------------------------------- #
# model 1: conservative price-through baseline
# --------------------------------------------------------------------------- #

@dataclass
class FillOutcome:
    filled: bool
    fill_ts: float = float("nan")
    fill_px: float = float("nan")
    horizon_s: float = 0.0
    queue_ahead0: float = float("nan")
    distance_bps0: float = float("nan")
    model: str = ""


class PriceThroughModel:
    """Baseline identical to ``RestingQuote.crossed``: only a strict move-through fills.

    Evaluated on the recorded stream, so ``p_fill`` is an *empirical* frequency
    over real start times -- no distributional assumption at all.
    """

    name = "price_through"

    def __init__(self, cap: Capture, symbol: str):
        self.cap = cap
        self.symbol = symbol
        b = cap.books(symbol)
        self.ts, self.bid, self.ask, self.mid = b["ts"], b["bid"], b["ask"], b["mid"]

    def first_through(self, side: str, px: float, t0: float, horizon_s: float) -> Optional[float]:
        """Timestamp of the first strict move-through after t0, or None."""
        if not len(self.ts):
            return None
        i0 = int(np.searchsorted(self.ts, t0, side="left"))
        i1 = int(np.searchsorted(self.ts, t0 + horizon_s, side="right"))
        if i1 <= i0:
            return None
        if side == "buy":
            hit = np.nonzero(self.ask[i0:i1] < px)[0]
        else:
            hit = np.nonzero(self.bid[i0:i1] > px)[0]
        return float(self.ts[i0 + hit[0]]) if len(hit) else None

    def p_fill(self, side: str, distance_bps: float, horizon_s: float,
               n_starts: int = 400, seed: int = 7) -> dict:
        """Empirical P(fill) and conditional fill time for a quote at `distance_bps`."""
        if not len(self.ts) or self.cap.duration_s() <= horizon_s:
            return {"model": self.name, "source": self.cap.source, "symbol": self.symbol,
                    "side": side, "distance_bps": distance_bps, "horizon_s": horizon_s,
                    "n_starts": 0, "p_fill": float("nan"), "note": "capture shorter than horizon"}
        rng = np.random.default_rng(seed)
        hi = self.cap.duration_s() - horizon_s
        starts = self.ts[0] + np.sort(rng.uniform(0, hi, size=min(n_starts, max(1, int(hi)))))
        fills, times = 0, []
        for t0 in starts:
            mid = float(_at(self.cap, self.symbol, t0, "mid"))
            px = mid * (1 - distance_bps / 1e4) if side == "buy" else mid * (1 + distance_bps / 1e4)
            ft = self.first_through(side, px, t0, horizon_s)
            if ft is not None:
                fills += 1
                times.append(ft - t0)
        n = len(starts)
        return {"model": self.name, "source": self.cap.source, "symbol": self.symbol, "side": side,
                "distance_bps": distance_bps, "horizon_s": horizon_s, "n_starts": int(n),
                "p_fill": fills / n if n else float("nan"),
                "mean_fill_s": float(np.mean(times)) if times else float("nan"),
                "median_fill_s": float(np.median(times)) if times else float("nan"),
                "p90_fill_s": float(np.quantile(times, 0.9)) if times else float("nan")}


# --------------------------------------------------------------------------- #
# model 2/3: queue depletion (analytic + Monte Carlo)
# --------------------------------------------------------------------------- #

@dataclass
class MidPath:
    """Mid-price path generator used by the Monte Carlo model."""

    mode: str                       # "bootstrap" | "ou" | "flat"
    step_s: float
    increments_bps: np.ndarray = field(default_factory=lambda: np.zeros(0))
    theta: float = 0.0
    sigma_bps: float = 0.0
    note: str = ""

    @staticmethod
    def fit(cap: Capture, symbol: str, mode: str = "bootstrap",
            step_s: float = 1.0) -> "MidPath":
        b = cap.books(symbol)
        if len(b["ts"]) < 10:
            return MidPath(mode="flat", step_s=step_s, note="insufficient book messages")
        # resample the mid onto a regular grid so increments are comparable
        grid = np.arange(b["ts"][0], b["ts"][-1], step_s)
        if len(grid) < 5:
            return MidPath(mode="flat", step_s=step_s, note="capture shorter than step")
        mid_g = np.interp(grid, b["ts"], b["mid"])
        ret = np.diff(mid_g) / mid_g[:-1] * 1e4
        ret = ret[np.isfinite(ret)]
        if mode == "ou":
            x, y = mid_g[:-2], np.diff(mid_g)[1:] / mid_g[:-2] * 1e4
            x = x - np.mean(mid_g)
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() > 5 and np.var(x[ok]) > 0:
                beta = float(np.cov(x[ok], y[ok])[0, 1] / np.var(x[ok]))
                theta = max(1e-6, -beta / step_s)
                sigma = float(np.std(y[ok] - beta * x[ok]) / math.sqrt(step_s))
                return MidPath(mode="ou", step_s=step_s, theta=theta, sigma_bps=sigma,
                               increments_bps=ret,
                               note=f"OU theta={theta:.4g}/s sigma={sigma:.3g}bps/sqrt(s)")
            return MidPath(mode="flat", step_s=step_s, note="OU fit degenerate")
        return MidPath(mode="bootstrap", step_s=step_s, increments_bps=ret,
                       note=f"bootstrap {len(ret)} increments, sd={float(np.std(ret)):.3g}bps/{step_s}s")

    def sample(self, rng: np.random.Generator, n_paths: int, n_steps: int,
               d0_bps: np.ndarray) -> np.ndarray:
        """Distance-to-mid paths (bps), shape (n_paths, n_steps).

        ``d`` is our quote's distance from the mid; a mid move *towards* us
        shrinks it.  For a buy at ``mid*(1-d/1e4)`` a mid fall of ``r`` bps
        changes ``d`` by ``+r`` (we get further from the touch).
        """
        if self.mode == "flat":
            return np.repeat(d0_bps[:, None], n_steps, axis=1)
        if self.mode == "ou":
            d = np.empty((n_paths, n_steps))
            cur = d0_bps.copy()
            shock = rng.standard_normal((n_paths, n_steps)) * self.sigma_bps * math.sqrt(self.step_s)
            for k in range(n_steps):
                cur = cur - self.theta * cur * self.step_s + shock[:, k]
                d[:, k] = cur
            return d
        inc = self.increments_bps
        if not len(inc):
            return np.repeat(d0_bps[:, None], n_steps, axis=1)
        drawn = inc[rng.integers(0, len(inc), size=(n_paths, n_steps))]
        return d0_bps[:, None] + np.cumsum(drawn, axis=1)


@dataclass
class QueueDepletionModel:
    """Analytic: ``P(fill <= h) = 1 - exp(-(pi(d) + (mu_trade+mu_cancel)/Q0) * h)``.

    Two competing exponential clocks: the price sweeps through us (``pi``), or the
    queue ahead drains (FIFO: trades + cancellations ahead of us, rate ``mu/Q0``).
    Deliberately memoryless -- that is the point of the baseline: it has one
    parameter per channel, both measured, and it cannot hide a shape assumption.
    """

    name = "queue_exponential"
    calib: Calibration
    edges: Sequence[float] = DEFAULT_BUCKETS
    queue_mult: float = 1.0            # sensitivity knob: Q0 * queue_mult
    use_cancel: bool = True            # sensitivity knob: ignore cancellations ahead

    def rates(self, distance_bps: float) -> tuple[float, float, float, float]:
        i = bucket_of(max(distance_bps, 0.0), self.edges)
        pi = float(self.calib.through_hazard_per_s[i])
        mu_tr = float(self.calib.trade_rate_base_per_s[i])
        mu_ca = float(self.calib.cancel_rate_base_per_s[i]) if self.use_cancel else 0.0
        q0 = float(self.calib.queue_base[i]) * self.queue_mult
        return pi, mu_tr, mu_ca, q0

    def p_fill(self, side: str, distance_bps: float, horizon_s: float,
               queue_base: Optional[float] = None) -> dict:
        pi, mu_tr, mu_ca, q0 = self.rates(distance_bps)
        if queue_base is not None:
            q0 = queue_base * self.queue_mult
        mu = mu_tr + mu_ca
        drain = mu / q0 if (np.isfinite(q0) and q0 > 0) else 0.0
        kappa = pi + drain
        p = 1.0 - math.exp(-kappa * horizon_s) if kappa > 0 else 0.0
        mean_t = (1.0 / kappa) * (1.0 - math.exp(-kappa * horizon_s) * (1 + kappa * horizon_s)) / p \
            if p > 1e-12 else float("nan")       # E[T | T<=h] for a truncated exponential
        i = bucket_of(max(distance_bps, 0.0), self.edges)
        return {"model": self.name, "source": self.calib.source, "symbol": self.calib.symbol,
                "side": side, "distance_bps": distance_bps, "horizon_s": horizon_s,
                "bucket": self.calib.buckets[i], "n_spells": float(self.calib.n_spells[i]),
                "queue_base": q0, "trade_rate": mu_tr, "cancel_rate": mu_ca,
                "through_hazard": pi, "drain_hazard": drain,
                "p_fill": float(p), "mean_fill_s": float(mean_t) if np.isfinite(mean_t) else float("nan"),
                "queue_mult": self.queue_mult, "use_cancel": self.use_cancel}


@dataclass
class QueueMonteCarloModel:
    """Same measured rates, but the distance is not frozen: the mid moves.

    Per step ``dt`` (the ``MidPath`` grid), for each simulated order:

    * the distance ``d`` is updated from the mid path (bootstrap or OU);
    * the queue ahead ``Q`` shrinks by ``Poisson((mu_trade+mu_cancel)(d) * dt)`` --
      FIFO means only size ahead of us can help us, and both channels do;
    * an independent Bernoulli with rate ``pi(d) * dt`` fires the price-through
      event (a sweep that eats the whole queue at once);
    * ``Q <= 0`` or a through event => filled at our price, at that time.

    A quote whose dislocation runs away simply stops draining (measured
    ``mu(d) -> 0`` at large ``d``) and is censored at the horizon.  That is the
    mechanism behind ``p_fail``, not an extra parameter.
    """

    name = "queue_monte_carlo"
    calib: Calibration
    mid_path: MidPath
    edges: Sequence[float] = DEFAULT_BUCKETS
    n_paths: int = 2000
    queue_mult: float = 1.0
    use_cancel: bool = True
    seed: int = 11

    def _rate(self, d: np.ndarray, which: str) -> np.ndarray:
        idx = np.clip(np.searchsorted(np.asarray(self.edges), np.maximum(d, 0.0), side="right") - 1,
                      0, len(self.edges) - 1)
        arr = {"trade": self.calib.trade_rate_base_per_s,
               "cancel": self.calib.cancel_rate_base_per_s,
               "through": self.calib.through_hazard_per_s}[which]
        out = np.asarray(arr, dtype=float)[idx]
        return np.nan_to_num(out, nan=0.0)

    def simulate(self, side: str, d0_bps: np.ndarray, horizon_s: float,
                 q0: Optional[np.ndarray] = None) -> dict:
        rng = np.random.default_rng(self.seed)
        d0_in = np.atleast_1d(np.asarray(d0_bps, dtype=float))
        # spread the path budget evenly over the requested start distances
        per = max(1, self.n_paths // len(d0_in))
        d0 = np.repeat(d0_in, per)[: self.n_paths]
        if len(d0) < self.n_paths:
            d0 = np.concatenate([d0, np.full(self.n_paths - len(d0), d0_in[0])])
        if q0 is None:
            idx = np.clip(np.searchsorted(np.asarray(self.edges), np.maximum(d0, 0.0), side="right") - 1,
                          0, len(self.edges) - 1)
            q = np.nan_to_num(np.asarray(self.calib.queue_base, dtype=float)[idx], nan=0.0)
            med = float(np.nanmedian(np.asarray(self.calib.queue_base))) or 1.0
            q = np.where(q > 0, q, med) * self.queue_mult
        else:
            q = np.full(len(d0), float(np.median(q0))) * self.queue_mult
        steps = max(1, int(math.ceil(horizon_s / self.mid_path.step_s)))
        dt = self.mid_path.step_s
        paths = self.mid_path.sample(rng, len(d0), steps, d0)
        Q = q.copy()
        fill_t = np.full(len(d0), np.nan)
        alive = np.ones(len(d0), dtype=bool)
        for k in range(steps):
            d = paths[:, k]
            lam_dep = (self._rate(d, "trade") + (self._rate(d, "cancel") if self.use_cancel else 0.0)) * dt
            draw = rng.poisson(np.maximum(lam_dep, 0.0))
            Q = Q - draw
            pi = np.minimum(self._rate(d, "through") * dt, 1.0)
            through = rng.random(len(d0)) < pi
            hit = alive & ((Q <= 0) | through)
            if hit.any():
                fill_t[hit] = (k + 1) * dt
                alive &= ~hit
        filled = np.isfinite(fill_t)
        p = float(filled.mean()) if len(d0) else float("nan")
        return {"model": self.name, "source": self.calib.source, "symbol": self.calib.symbol,
                "side": side, "horizon_s": horizon_s, "n_paths": int(len(d0)),
                "mid_mode": self.mid_path.mode, "step_s": dt,
                "p_fill": p,
                "mean_fill_s": float(fill_t[filled].mean()) if filled.any() else float("nan"),
                "median_fill_s": float(np.median(fill_t[filled])) if filled.any() else float("nan"),
                "p10_fill_s": float(np.quantile(fill_t[filled], 0.10)) if filled.any() else float("nan"),
                "p90_fill_s": float(np.quantile(fill_t[filled], 0.90)) if filled.any() else float("nan"),
                "queue_mult": self.queue_mult, "use_cancel": self.use_cancel,
                "d0_bps_median": float(np.median(d0)) if len(d0) else float("nan")}

    def p_fill(self, side: str, distance_bps: float, horizon_s: float,
               queue_base: Optional[float] = None) -> dict:
        out = self.simulate(side, np.array([distance_bps]), horizon_s,
                            q0=np.array([queue_base]) if queue_base else None)
        out["distance_bps"] = distance_bps
        if queue_base:
            out["queue_base"] = queue_base
        return out


# --------------------------------------------------------------------------- #
# table builders used by findings.md
# --------------------------------------------------------------------------- #

def p_fill_table(cap: Capture, symbol: str, side: str, distances_bps: Sequence[float],
                 horizons_s: Sequence[float] = (60.0, 300.0, 1800.0),
                 n_paths: int = 1500, seed: int = 11) -> list[dict]:
    """Baseline vs analytic queue model vs MC queue model, per distance x horizon."""
    calib = calibrate(cap, symbol, side)
    base = PriceThroughModel(cap, symbol)
    anal = QueueDepletionModel(calib=calib)
    mc = QueueMonteCarloModel(calib=calib, mid_path=MidPath.fit(cap, symbol, "bootstrap"),
                              n_paths=n_paths, seed=seed)
    rows: list[dict] = []
    for d in distances_bps:
        for h in horizons_s:
            if h > cap.duration_s():
                continue
            r = base.p_fill(side, d, h, seed=seed)
            a = anal.p_fill(side, d, h)
            m = mc.p_fill(side, d, h)
            rows.append({"symbol": symbol, "side": side, "distance_bps": d, "horizon_s": h,
                         "source": cap.source,
                         "p_fill_price_through": r.get("p_fill"),
                         "n_starts": r.get("n_starts"),
                         "p_fill_queue_analytic": a.get("p_fill"),
                         "p_fill_queue_mc": m.get("p_fill"),
                         "mean_fill_s_analytic": a.get("mean_fill_s"),
                         "mean_fill_s_mc": m.get("mean_fill_s"),
                         "queue_base": a.get("queue_base"),
                         "trade_rate": a.get("trade_rate"), "cancel_rate": a.get("cancel_rate"),
                         "through_hazard": a.get("through_hazard")})
    return rows


def sensitivity_table(cap: Capture, symbol: str, side: str, distances_bps: Sequence[float],
                      horizon_s: float = 300.0, queue_mults: Sequence[float] = (0.5, 1.0, 2.0),
                      mid_modes: Sequence[str] = ("bootstrap", "ou"),
                      n_paths: int = 1200) -> list[dict]:
    """How much of the answer is the data, and how much is the parameterisation."""
    calib = calibrate(cap, symbol, side)
    rows = []
    for qm in queue_mults:
        anal = QueueDepletionModel(calib=calib, queue_mult=qm)
        for use_cancel in (True, False):
            anal.use_cancel = use_cancel
            for d in distances_bps:
                r = anal.p_fill(side, d, horizon_s)
                rows.append({"variant": "analytic", "queue_mult": qm, "use_cancel": use_cancel,
                             "mid_mode": "n/a", "distance_bps": d, "horizon_s": horizon_s,
                             "source": cap.source, "p_fill": r["p_fill"],
                             "mean_fill_s": r["mean_fill_s"]})
    for mode in mid_modes:
        mp = MidPath.fit(cap, symbol, mode)
        for qm in queue_mults:
            mc = QueueMonteCarloModel(calib=calib, mid_path=mp, queue_mult=qm, n_paths=n_paths)
            for d in distances_bps:
                r = mc.p_fill(side, d, horizon_s)
                rows.append({"variant": "monte_carlo", "queue_mult": qm, "use_cancel": True,
                             "mid_mode": mp.mode, "distance_bps": d, "horizon_s": horizon_s,
                             "source": cap.source, "p_fill": r["p_fill"],
                             "mean_fill_s": r["mean_fill_s"], "mid_note": mp.note})
    return rows


# --------------------------------------------------------------------------- #
# synthetic market (for calibration validation and the OKX leg, which is unreachable)
# --------------------------------------------------------------------------- #

def synthetic_capture(symbol: str = "SYNTH", duration_s: float = 1800.0, step_s: float = 0.5,
                      mid0: float = 100.0, sigma_bps_per_sqrt_s: float = 6.0,
                      half_spread_bps: float = 2.0, theta: float = 0.0,
                      touch_size_base: float = 8.0, depth_decay: float = 0.35,
                      trade_intensity_per_s: float = 0.6, trade_size_base: float = 1.2,
                      cancel_intensity_per_s: float = 0.25, cancel_frac: float = 0.5,
                      seed: int = 3, order_poll_s: float = 2.0,
                      mid_path: Optional[tuple[np.ndarray, np.ndarray]] = None) -> Capture:
    """An OU mid + Poisson taker flow + Poisson cancels, with *known* rates.

    Used for two things only: (a) proving the calibrator recovers rates it did not
    get to choose, and (b) standing in for the OKX leg while OKX is unreachable.
    Every result derived from it must carry ``source="synthetic"``.

    ``mid_path=(ts, mid)`` overrides the generated OU mid -- that is how the
    harness builds an OKX leg whose mid is the *recorded* Lighter mid minus a
    prescribed basis, so the two legs share one price of record.
    """
    rng = np.random.default_rng(seed)
    if mid_path is not None:
        ts = np.asarray(mid_path[0], dtype=float)
        mid = np.asarray(mid_path[1], dtype=float)
        n = len(ts)
        duration_s = float(ts[-1] - ts[0])
        step_s = float(np.median(np.diff(ts))) if n > 1 else step_s
    else:
        n = int(duration_s / step_s)
        ts = np.arange(n) * step_s + 1_700_000_000.0
        # OU mid in bps space
        d_mid = np.zeros(n)
        for i in range(1, n):
            d_mid[i] = d_mid[i - 1] - theta * d_mid[i - 1] * step_s \
                + rng.standard_normal() * sigma_bps_per_sqrt_s * math.sqrt(step_s)
        mid = mid0 * (1 + d_mid / 1e4)
    hs = half_spread_bps / 1e4
    bid = mid * (1 - hs)
    ask = mid * (1 + hs)
    # queue at the touch: mean-reverting size
    q_bid = np.full(n, touch_size_base)
    q_ask = np.full(n, touch_size_base)
    for i in range(1, n):
        for arr in (q_bid, q_ask):
            arr[i] = max(0.0, arr[i - 1] + (touch_size_base - arr[i - 1]) * depth_decay * step_s
                         + rng.standard_normal() * touch_size_base * 0.15 * math.sqrt(step_s))
    cap = Capture(source="synthetic", symbols=[symbol], symbol_ids={symbol: 0},
                  book_ts=ts, book_sym=np.zeros(n, dtype=int), book_bid=bid, book_ask=ask,
                  book_mid=mid, book_bid_sz=q_bid, book_ask_sz=q_ask,
                  book_spread_bps=np.full(n, 2 * half_spread_bps),
                  book_latency_ms=np.full(n, 25.0), book_offset_gap=np.zeros(n, dtype=int),
                  meta={"synthetic_params": {
                      "sigma_bps_per_sqrt_s": sigma_bps_per_sqrt_s, "theta": theta,
                      "half_spread_bps": half_spread_bps, "touch_size_base": touch_size_base,
                      "trade_intensity_per_s": trade_intensity_per_s,
                      "trade_size_base": trade_size_base,
                      "cancel_intensity_per_s": cancel_intensity_per_s,
                      "cancel_frac": cancel_frac, "step_s": step_s}})
    # trades: Poisson, exponential size, side chosen by the mid move sign
    n_trades = rng.poisson(trade_intensity_per_s * duration_s)
    t_ts = np.sort(rng.uniform(ts[0], ts[-1], size=n_trades))
    t_sz = rng.exponential(trade_size_base, size=n_trades)
    t_px = np.where(rng.random(n_trades) < 0.5,
                    np.interp(t_ts, ts, bid), np.interp(t_ts, ts, ask))
    maker_ask = t_px >= np.interp(t_ts, ts, mid)
    cap.trade_ts, cap.trade_sym = t_ts, np.zeros(n_trades, dtype=int)
    cap.trade_px, cap.trade_sz, cap.trade_maker_ask = t_px, t_sz, maker_ask
    # order-level polls: resting orders with monotone order_index, cancels applied
    idx = 1_000_000
    poll_ts = np.arange(ts[0], ts[-1], order_poll_s)
    rows = []
    live: dict[int, tuple[float, float, float, int]] = {}   # idx -> (px, rem, init, side)
    for p_ts in poll_ts:
        i = int(np.searchsorted(ts, p_ts))
        i = min(i, n - 1)
        # retire some orders (cancellation) and add new ones
        for k in list(live):
            if rng.random() < cancel_intensity_per_s * cancel_frac * order_poll_s:
                live.pop(k)
        for _ in range(rng.poisson(trade_intensity_per_s * order_poll_s * 2)):
            side = 1 if rng.random() < 0.5 else -1
            px = float(ask[i]) if side == 1 else float(bid[i])
            idx += 1
            rem = float(rng.exponential(trade_size_base))
            live[idx] = (px, rem, rem, side)
        for k, (px, rem, init, side) in live.items():
            rows.append((float(p_ts), 0, k, px, rem, init, side, float(mid[i])))
    if rows:
        o = np.array(rows, dtype=float)
        cap.order_ts, cap.order_sym = o[:, 0], o[:, 1].astype(int)
        cap.order_index = o[:, 2].astype(np.int64)
        cap.order_px, cap.order_remaining, cap.order_initial = o[:, 3], o[:, 4], o[:, 5]
        cap.order_side, cap.order_mid = o[:, 6].astype(int), o[:, 7]
    return cap


def write_capture(cap: Capture, directory: str) -> None:
    """Persist a (synthetic) capture in the recorder's JSONL layout, for reuse."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "book.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(len(cap.book_ts)):
            fh.write(json.dumps({
                "recv_ts": float(cap.book_ts[i]), "symbol": cap.symbols[int(cap.book_sym[i])],
                "market_id": int(cap.book_sym[i]), "bid": float(cap.book_bid[i]),
                "ask": float(cap.book_ask[i]), "mid": float(cap.book_mid[i]),
                "bid_sz": float(cap.book_bid_sz[i]), "ask_sz": float(cap.book_ask_sz[i]),
                "spread_bps": float(cap.book_spread_bps[i]),
                "latency_ms": float(cap.book_latency_ms[i]), "src": "synthetic"}) + "\n")
    with open(os.path.join(directory, "trades.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(len(cap.trade_ts)):
            fh.write(json.dumps({
                "recv_ts": float(cap.trade_ts[i]), "symbol": cap.symbols[int(cap.trade_sym[i])],
                "market_id": int(cap.trade_sym[i]), "venue_ts_ms": float(cap.trade_ts[i]) * 1000,
                "px": float(cap.trade_px[i]), "sz": float(cap.trade_sz[i]),
                "is_maker_ask": bool(cap.trade_maker_ask[i]), "src": "synthetic"}) + "\n")
    with open(os.path.join(directory, "book_orders.jsonl"), "w", encoding="utf-8") as fh:
        polls = np.unique(cap.order_ts) if len(cap.order_ts) else np.zeros(0)
        for p_ts in polls:
            m = cap.order_ts == p_ts
            sym = cap.symbols[int(cap.order_sym[m][0])] if m.any() else cap.symbols[0]
            orders = [[int(i), float(px), float(r), float(ini), int(s)] for i, px, r, ini, s
                      in zip(cap.order_index[m], cap.order_px[m], cap.order_remaining[m],
                             cap.order_initial[m], cap.order_side[m])]
            fh.write(json.dumps({"recv_ts": float(p_ts), "symbol": sym, "market_id": 0,
                                 "mid": float(cap.order_mid[m][0]) if m.any() else 0.0,
                                 "orders": orders, "src": "synthetic"}) + "\n")
    with open(os.path.join(directory, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"source": "synthetic", **cap.meta}, fh, indent=2)
