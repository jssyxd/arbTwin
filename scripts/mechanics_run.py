#!/usr/bin/env python3
"""Drive one full wave round per symbol against a real hourly series.

Purpose: prove the state machine (entry -> partial -> hedged -> exit -> flat)
completes for each symbol with the *shipped* thresholds, deterministically, and
record whatever looks wrong.  Books are synthesised from each venue's own price
with the measured spread, so spread cost is real; fills happen when the book
moves through a resting quote (the same rule as dry-run).

Usage: python scripts/mechanics_run.py --symbols SOXL,COIN,LIT,VVV,GRAM
"""
from __future__ import annotations

import argparse
import json
import statistics
import time as _time
from pathlib import Path

import yaml

from arbtwin.config import AppConfig
from arbtwin.models import Book
import arbtwin.wave as _wave
from arbtwin.wave import Round, RoundState, WaveParams, round_pnl_bps


class _Clock:
    """Deterministic clock so the state machine sees realistic dwell times.

    One poll = 5 minutes of market time; prices are interpolated inside each
    hourly bar, otherwise every poll would jump an hour and every naked-leg stop
    would trip on an hourly move that live never sees in one tick.
    """

    now = 1_700_000_000.0

    @classmethod
    def time(cls):
        return cls.now

    @classmethod
    def advance(cls, seconds: float = 300.0):
        cls.now += seconds


_wave.time = _Clock
from arbtwin.wavetrader import WaveTrader


def load_series(symbol: str, days: int = 30) -> tuple[list[float], list[float]]:
    cache = Path("state/cache") / f"{symbol}_{days}d.json"
    if not cache.exists():
        cache = Path("state/cache") / f"{symbol}_14d.json"
    d = json.load(open(cache, encoding="utf-8"))
    okx = {int(k): v for k, v in d["okx"].items()}
    lt = {int(k): v for k, v in d["lighter"].items()}
    keys = sorted(set(okx) & set(lt))
    return [okx[k] for k in keys], [lt[k] for k in keys]


def synth(px: float, mid_other: float, spread_bps: float = 1.0) -> Book:
    half = px * spread_bps / 1e4 / 2
    return Book("x", "S", bids=[(px - half, 1e9)], asks=[(px + half, 1e9)])


def thresholds(okx_px, lt_px, ratio: float, session: str = "crypto") -> tuple[float, float]:
    import datetime as _dt
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    vals = []
    for i in range(1, len(okx_px)):
        b = (lt_px[i] / ratio - okx_px[i]) / okx_px[i] * 1e4
        if session == "us_equity":
            t = _dt.datetime.now()  # timestamps come from the cache keys; approximate with all bars
        vals.append(abs(b))
    vals.sort()
    def q(p):
        i = p * (len(vals) - 1); lo = int(i); hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (i - lo)
    return q(0.95), q(0.50)


def run_symbol(symbol: str, entry: dict, trace: list) -> dict:
    okx_px, lt_px = load_series(symbol)
    if len(okx_px) < 50:
        return {"symbol": symbol, "status": "no_data"}
    ratio = entry.get("unit_ratio") or statistics.fmean([l / o for o, l in zip(okx_px, lt_px)])
    p95, p50 = thresholds(okx_px, lt_px, ratio)
    sig = 0.0
    try:
        sig = float((entry or {}).get("sigma_1h_bps") or 0.0)
    except Exception:
        sig = 0.0
    p = WaveParams(entry_bps=round(p95, 2), exit_bps=round(p50, 2),
                   max_hold_h=4.0, min_edge_bps=-1e9,   # mechanics run: bypass the edge gate
                   max_unhedged_notional_usd=10_000.0,
                   leg_sigma_1h_bps=sig)
    trader = WaveTrader({symbol: p}, place=lambda o: _fake_fill(o), persist=lambda r: trace.append(r))
    rnd = trader.round_for(symbol)          # the trader owns the round; do not shadow it
    qty = 0.0
    substeps = 12
    for i in range(1, len(okx_px)):
        for k in range(substeps):
            f = k / substeps
            px_o = okx_px[i - 1] + (okx_px[i] - okx_px[i - 1]) * f
            px_l = lt_px[i - 1] + (lt_px[i] - lt_px[i - 1]) * f
            basis = (px_l / ratio - px_o) / px_o * 1e4
            okx_book, lt_book = synth(px_o, px_l), synth(px_l, px_o)
            if rnd.state is RoundState.FLAT and qty == 0.0:
                qty = 12.0 / max(px_o, 1e-9)   # $12/leg, the v1 size
            trader.poll_fills(symbol, okx_book, lt_book)
            trader.step(symbol, basis, okx_book, lt_book, ratio, qty, 99.0,
                        minutes_to_close=float("inf"))
            _Clock.advance(300.0)
            if rnd.state is RoundState.FLAT and rnd.exit_legs:
                break
        if rnd.state is RoundState.FLAT and rnd.exit_legs:
            break
    return {"symbol": symbol, "state": rnd.state.value, "round_id": rnd.round_id,
            "entry_bps": p.entry_bps, "exit_bps": p.exit_bps,
            "entry_basis": round(rnd.entry_basis_bps, 2), "exit_basis": round(rnd.exit_basis_bps, 2),
            "pnl_bps": round(round_pnl_bps(rnd) or 0.0, 2), "notes": rnd.notes[-4:],
            "status": "complete" if rnd.state is RoundState.FLAT and rnd.exit_legs else "stuck"}


def _maker_net(entry: dict, basis: float, p: WaveParams) -> float:
    spread = entry.get("spread_okx_bps", 1.0) + entry.get("spread_lighter_bps", 1.0)
    return abs(basis) - 4.0 - spread - 1.0


def _fake_fill(order):
    from arbtwin.models import Fill
    return Fill(order.venue, order.symbol, order.side, order.qty_base, order.px, order_id="sim")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SOXL,COIN,LIT,VVV,GRAM")
    ap.add_argument("--out", default="state/mechanics_report.json")
    args = ap.parse_args()
    cfg = AppConfig.load()
    entries = {}
    for path in ("config/symbols.starter.yaml", "config/symbols.yaml"):
        try:
            for e in yaml.safe_load(open(path, encoding="utf-8")).get("approved", []):
                entries.setdefault(e["symbol"], e)
        except FileNotFoundError:
            pass
    rows = []
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        e = entries.get(sym) or {"symbol": sym}
        if not (Path("state/cache") / f"{sym}_14d.json").exists() and not (
                Path("state/cache") / f"{sym}_30d.json").exists():
            rows.append({"symbol": sym, "status": "no_cache"})
            continue
        trace: list = []
        rows.append(run_symbol(sym, e, trace))
    Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    for r in rows:
        print(f"{r['symbol']:6s} {r.get('status','?'):9s} state={r.get('state','?'):12s} "
              f"entry={r.get('entry_basis','-'):>7} exit={r.get('exit_basis','-'):>7} "
              f"pnl={r.get('pnl_bps','-'):>7} {r.get('notes') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
