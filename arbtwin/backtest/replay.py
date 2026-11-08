"""Causal replay of the OKX <-> Lighter basis.

Rules that make the result honest:

* the signal at bar `i` uses closes up to and including `i`;
* the fill happens at the close of bar `i+1` (never at `i`), with slippage and
  the full round-trip fee applied on entry *and* exit;
* the band is recomputed from the trailing window only (no full-sample sigma);
* a position that still exists at the end of the sample is marked to market and
  counted as a trade — no silently dropped losers.

It is not an execution simulator (no queue position, no depth walk).  It exists
to answer one question before deploying: *does the basis ever reach a number
that covers fees plus a defensible band, often enough to matter?*
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from ..band import entry_band_bps, exit_band_bps, returns_from_closes, sigma_1h_bps
from ..calibration import percentile
from ..config import BandParams


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    direction: str
    entry_basis_bps: float
    exit_basis_bps: float
    gross_bps: float
    net_bps: float
    bars_held: int


@dataclass
class ReplayResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    n_bars: int = 0
    total_net_bps: float = 0.0
    win_rate: float = 0.0
    avg_net_bps: float = 0.0
    max_drawdown_bps: float = 0.0
    basis_p50: float = 0.0
    basis_p95: float = 0.0
    hours_in_band_pct: float = 0.0

    def stats(self) -> dict:
        return {
            "symbol": self.symbol,
            "n_bars": self.n_bars,
            "n_trades": len(self.trades),
            "total_net_bps": round(self.total_net_bps, 2),
            "avg_net_bps": round(self.avg_net_bps, 2),
            "win_rate": round(self.win_rate, 4),
            "max_drawdown_bps": round(self.max_drawdown_bps, 2),
            "basis_p50_bps": round(self.basis_p50, 2),
            "basis_p95_bps": round(self.basis_p95, 2),
            "hours_in_band_pct": round(self.hours_in_band_pct, 4),
            "avg_hold_bars": round(statistics.fmean([t.bars_held for t in self.trades]), 2) if self.trades else 0.0,
        }


def replay(symbol: str, okx_closes: dict[int, float], lighter_closes: dict[int, float],
           unit_ratio: float, fee_round_trip_bps: float, params: Optional[BandParams] = None,
           latency_bps: float = 0.0, slippage_bps: float = 2.0,
           lookback: int = 168, max_hold_bars: int = 72,
           exit_override: float | None = None) -> ReplayResult:
    p = params or BandParams()
    res = ReplayResult(symbol=symbol)
    if unit_ratio <= 0:
        return res

    keys = sorted(set(okx_closes) & set(lighter_closes))
    if len(keys) < lookback + 10:
        return res
    ok = [okx_closes[k] for k in keys]
    lt = [lighter_closes[k] for k in keys]
    basis = [(lt[i] / unit_ratio - ok[i]) / ok[i] * 1e4 for i in range(len(keys))]
    res.n_bars = len(keys)
    res.basis_p50 = statistics.median(basis)
    res.basis_p95 = percentile([abs(b) for b in basis], 0.95)

    cost = fee_round_trip_bps + latency_bps + slippage_bps * 2
    equity, peak, max_dd = 0.0, 0.0, 0.0
    in_band = 0
    pos: dict | None = None

    for i in range(lookback, len(keys) - 1):
        window_rets = returns_from_closes(ok[i - lookback:i + 1])
        sigma = sigma_1h_bps(window_rets)
        band = entry_band_bps(sigma, fee_round_trip_bps, p)
        exit_band = exit_override if exit_override is not None else exit_band_bps(band, p)
        b = basis[i]
        if abs(b) >= band:
            in_band += 1

        if pos is None:
            if abs(b) >= band + cost:
                direction = "short_basis" if b > 0 else "long_basis"
                pos = {"i": i, "entry": b, "direction": direction, "band": band}
            continue

        held = i - pos["i"]
        captured = (pos["entry"] - b) if pos["direction"] == "short_basis" else (b - pos["entry"])
        if captured >= exit_band or held >= max_hold_bars:
            gross = captured
            net = gross - cost
            equity += net
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
            res.trades.append(Trade(keys[pos["i"] + 1], keys[i + 1], pos["direction"],
                                    pos["entry"], b, gross, net, held))
            pos = None

    if pos is not None:                       # mark the leftover position to market
        b = basis[-1]
        captured = (pos["entry"] - b) if pos["direction"] == "short_basis" else (b - pos["entry"])
        net = captured - cost
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        res.trades.append(Trade(keys[pos["i"] + 1], keys[-1], pos["direction"],
                                pos["entry"], b, captured, net, len(keys) - 1 - pos["i"]))

    res.total_net_bps = equity
    res.max_drawdown_bps = max_dd
    res.hours_in_band_pct = in_band / max(1, len(keys) - lookback)
    if res.trades:
        res.avg_net_bps = statistics.fmean([t.net_bps for t in res.trades])
        res.win_rate = sum(1 for t in res.trades if t.net_bps > 0) / len(res.trades)
    return res


def walk_forward(okx_closes: dict[int, float], lighter_closes: dict[int, float],
                 unit_ratio: float, fee_round_trip_bps: float, folds: int = 4,
                 params: Optional[BandParams] = None) -> list[dict]:
    """Split the sample into consecutive folds; a stable edge survives all of them."""
    keys = sorted(set(okx_closes) & set(lighter_closes))
    if len(keys) < folds * 60:
        return []
    size = len(keys) // folds
    out = []
    for f in range(folds):
        lo, hi = f * size, (f + 1) * size if f < folds - 1 else len(keys)
        a = {k: okx_closes[k] for k in keys[lo:hi]}
        b = {k: lighter_closes[k] for k in keys[lo:hi]}
        r = replay("fold", a, b, unit_ratio, fee_round_trip_bps, params)
        s = r.stats()
        s["fold"] = f
        out.append(s)
    return out