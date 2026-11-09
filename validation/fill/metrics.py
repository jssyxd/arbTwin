"""Markout, p_fail, time-to-pair and capture-fraction metrics (validation T2).

Conventions, stated once because getting a sign wrong here silently inverts every
conclusion:

* ``side="buy"`` means *we* bought (we were the resting bid, or the taker of an ask).
  A favourable markout is the mid going **up** afterwards.
* ``markout_bps(h) = sign * (mid(t_fill + h) - fill_px) / fill_px * 1e4``,
  ``sign = +1`` for buy, ``-1`` for sell.  Positive = we were paid to be filled
  (no adverse selection); negative = the market moved through us.
* ``markout_mid_bps(h)`` uses the mid *at fill time* instead of the fill price, so
  it strips the half-spread we earn as a maker and isolates pure adverse selection.
  This is the number comparable to the -0.39..-0.84 bp touch-quote figures in
  `.field-guide/microstructure.md`.
* ``p_fail`` = P(second leg not filled within ``entry_deadline``  |  first leg filled).
  It is conditional on a first fill: an entry that never fills anything is not a
  failure, it is a non-event (and `capture_fraction` is undefined for it).
* ``capture_fraction`` = realised entry basis / basis quoted at signal time, on the
  same notional.  1.0 = we got the whole dislocation at the touch on both legs;
  <1.0 = queueing/re-pegging/partial fills ate part of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .fill_model import Capture, distance_bps

MARKOUT_HORIZONS_S = (60.0, 300.0, 1800.0)          # +1 / +5 / +30 min


# --------------------------------------------------------------------------- #
# mid lookup
# --------------------------------------------------------------------------- #

class MidSeries:
    """Step-function mid for one venue/symbol, with as-of lookup."""

    def __init__(self, ts: np.ndarray, mid: np.ndarray, label: str = ""):
        order = np.argsort(ts, kind="stable")
        self.ts = np.asarray(ts, dtype=float)[order]
        self.mid = np.asarray(mid, dtype=float)[order]
        self.label = label

    @staticmethod
    def from_capture(cap: Capture, symbol: str, venue: str = "lighter") -> "MidSeries":
        b = cap.books(symbol)
        return MidSeries(b["ts"], b["mid"], label=f"{venue}:{symbol}:{cap.source}")

    def at(self, t: float) -> float:
        if not len(self.ts):
            return float("nan")
        i = int(np.searchsorted(self.ts, t, side="right")) - 1
        if i < 0:
            return float(self.mid[0]) if t >= self.ts[0] - 1e-9 else float("nan")
        return float(self.mid[i])

    def span(self) -> tuple[float, float]:
        return (float(self.ts[0]), float(self.ts[-1])) if len(self.ts) else (0.0, 0.0)


# --------------------------------------------------------------------------- #
# fills / rounds as consumed by the metrics
# --------------------------------------------------------------------------- #

@dataclass
class FillEvent:
    venue: str
    symbol: str
    side: str                 # our side: buy | sell
    px: float
    qty_base: float
    ts: float
    distance_bps: float = float("nan")   # how far inside our own side we rested
    resting: bool = True                  # False = IOC/marketable escalation
    slice_id: int = 0
    source: str = "captured"              # "captured" | "synthetic"


@dataclass
class RoundOutcome:
    symbol: str
    round_id: int = 0
    signal_ts: float = 0.0
    signal_basis_bps: float = float("nan")
    first_fill_ts: float = float("nan")
    pair_ts: float = float("nan")          # both entry legs complete
    exit_ts: float = float("nan")
    state: str = "flat"
    realized_entry_basis_bps: float = float("nan")
    aborted: bool = False
    abort_cost_bps: float = float("nan")
    n_slices: int = 1
    slices_filled: int = 0
    fills: list[FillEvent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = "captured"

    @property
    def time_to_pair_s(self) -> float:
        if not (np.isfinite(self.first_fill_ts) and np.isfinite(self.pair_ts)):
            return float("nan")
        return float(self.pair_ts - self.first_fill_ts)

    @property
    def naked_s(self) -> float:
        """How long risk was one-legged: pair time, or (exit/signal end) if aborted."""
        if np.isfinite(self.time_to_pair_s):
            return self.time_to_pair_s
        if np.isfinite(self.first_fill_ts):
            end = self.exit_ts if np.isfinite(self.exit_ts) else self.signal_ts
            return float(max(0.0, end - self.first_fill_ts))
        return float("nan")


# --------------------------------------------------------------------------- #
# markout
# --------------------------------------------------------------------------- #

def markout(fill: FillEvent, mids: dict[str, MidSeries],
            horizons_s: Sequence[float] = MARKOUT_HORIZONS_S) -> dict:
    """Markout vs fill price and vs mid-at-fill, per horizon, in bps."""
    ms = mids.get(fill.venue)
    sign = 1.0 if fill.side == "buy" else -1.0
    out = {"venue": fill.venue, "symbol": fill.symbol, "side": fill.side, "px": fill.px,
           "ts": fill.ts, "source": fill.source, "distance_bps": fill.distance_bps,
           "resting": fill.resting, "slice_id": fill.slice_id}
    if ms is None:
        out["error"] = f"no mid series for venue {fill.venue}"
        return out
    mid0 = ms.at(fill.ts)
    out["mid_at_fill"] = mid0
    out["half_spread_captured_bps"] = sign * (mid0 - fill.px) / fill.px * 1e4 \
        if (np.isfinite(mid0) and fill.px) else float("nan")
    for h in horizons_s:
        mid_h = ms.at(fill.ts + h)
        if not np.isfinite(mid_h):
            out[f"markout_bps_{int(h)}s"] = float("nan")
            out[f"markout_mid_bps_{int(h)}s"] = float("nan")
            continue
        out[f"markout_bps_{int(h)}s"] = sign * (mid_h - fill.px) / fill.px * 1e4
        out[f"markout_mid_bps_{int(h)}s"] = sign * (mid_h - mid0) / mid0 * 1e4 if mid0 else float("nan")
    return out


def markout_curve(fills: Sequence[FillEvent], mids: dict[str, MidSeries],
                  horizons_s: Sequence[float] = MARKOUT_HORIZONS_S,
                  by_distance: bool = True,
                  dist_edges: Sequence[float] = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 1e9)) -> list[dict]:
    """Markout aggregated by horizon (and optionally by resting distance)."""
    rows: list[dict] = []
    scored = []
    for f in fills:
        m = markout(f, mids, horizons_s)
        scored.append((f, m))
    groups: dict[str, list[tuple[FillEvent, dict]]] = {"all": scored}
    if by_distance:
        for i in range(len(dist_edges)):
            lo = dist_edges[i - 1] if i else -1e9
            hi = dist_edges[i]
            g = [(f, m) for f, m in scored
                 if (not np.isfinite(f.distance_bps)) or (lo <= f.distance_bps < hi)]
            if g:
                groups[f"d[{lo:g},{hi:g})"] = g
    for name, g in groups.items():
        row = {"group": name, "n_fills": len(g),
               "n_resting": sum(1 for f, _ in g if f.resting),
               "source": sorted({f.source for f, _ in g})}
        for h in horizons_s:
            for key in (f"markout_bps_{int(h)}s", f"markout_mid_bps_{int(h)}s"):
                vals = np.array([m.get(key, np.nan) for _, m in g], dtype=float)
                vals = vals[np.isfinite(vals)]
                row[f"{key}_mean"] = round(float(vals.mean()), 4) if len(vals) else None
                row[f"{key}_median"] = round(float(np.median(vals)), 4) if len(vals) else None
                row[f"{key}_p10"] = round(float(np.quantile(vals, 0.10)), 4) if len(vals) >= 5 else None
                row[f"{key}_frac_negative"] = round(float((vals < 0).mean()), 4) if len(vals) else None
                row[f"{key}_n"] = int(len(vals))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# p_fail / time-to-pair / capture fraction
# --------------------------------------------------------------------------- #

def p_fail(rounds: Sequence[RoundOutcome], entry_deadline_s: float) -> dict:
    """P(second leg not filled within the deadline | first leg filled)."""
    started = [r for r in rounds if np.isfinite(r.first_fill_ts)]
    if not started:
        return {"entry_deadline_s": entry_deadline_s, "n_first_filled": 0, "p_fail": float("nan"),
                "note": "no round ever got a first fill"}
    failed = [r for r in started
              if (not np.isfinite(r.pair_ts)) or (r.pair_ts - r.first_fill_ts) > entry_deadline_s]
    aborted = [r for r in failed if r.aborted]
    n = len(started)
    rate = len(failed) / n
    return {"entry_deadline_s": entry_deadline_s, "n_first_filled": n,
            "n_failed": len(failed), "p_fail": rate,
            "n_aborted": len(aborted),
            "p_fail_se": math.sqrt(max(1e-12, rate * (1 - rate) / n)),
            "source": sorted({r.source for r in rounds})}


def p_fail_curve(rounds: Sequence[RoundOutcome],
                 deadlines_s: Sequence[float] = (30.0, 60.0, 300.0, 900.0, 1800.0, 3600.0, 5400.0),
                 ) -> list[dict]:
    return [p_fail(rounds, d) for d in deadlines_s]


def time_to_pair(rounds: Sequence[RoundOutcome],
                 quantiles: Sequence[float] = (0.1, 0.25, 0.5, 0.75, 0.9)) -> dict:
    lags = np.array([r.time_to_pair_s for r in rounds if np.isfinite(r.time_to_pair_s)], dtype=float)
    naked = np.array([r.naked_s for r in rounds if np.isfinite(r.naked_s)], dtype=float)
    n_all = sum(1 for r in rounds if np.isfinite(r.first_fill_ts))
    out = {"n_paired": int(len(lags)), "n_first_filled": int(n_all),
           "pair_rate": (len(lags) / n_all) if n_all else float("nan"),
           "naked_leg_exposure_s": {"n": int(len(naked)),
                                    "mean": round(float(naked.mean()), 1) if len(naked) else None,
                                    "p50": round(float(np.median(naked)), 1) if len(naked) else None,
                                    "p90": round(float(np.quantile(naked, 0.9)), 1) if len(naked) else None}}
    for q in quantiles:
        out[f"pair_lag_p{int(q * 100)}_s"] = round(float(np.quantile(lags, q)), 2) if len(lags) else None
    out["pair_lag_mean_s"] = round(float(lags.mean()), 2) if len(lags) else None
    out["source"] = sorted({r.source for r in rounds})
    return out


def capture_fraction(rounds: Sequence[RoundOutcome]) -> dict:
    """Realised entry basis vs the basis that triggered the round.

    Only rounds that actually paired have a realised entry basis; a round that
    aborted contributes `capture = 0` *and* its abort cost, because pretending it
    captured nothing without costing anything is how `MECHANICS-ROUNDS` ended up
    with pnl=0 on 4 of 5 aborts.
    """
    paired = [r for r in rounds if np.isfinite(r.realized_entry_basis_bps) and r.signal_basis_bps]
    aborted = [r for r in rounds if r.aborted]
    fr = np.array([r.realized_entry_basis_bps / r.signal_basis_bps for r in paired], dtype=float)
    fr = fr[np.isfinite(fr)]
    sig = np.array([r.signal_basis_bps for r in paired], dtype=float)
    real = np.array([r.realized_entry_basis_bps for r in paired], dtype=float)
    notional_w = np.abs(sig)
    out = {"n_rounds": len(rounds), "n_paired": len(paired), "n_aborted": len(aborted),
           "abort_rate": (len(aborted) / len(rounds)) if rounds else float("nan"),
           "signal_basis_bps_mean": round(float(sig.mean()), 3) if len(sig) else None,
           "realized_entry_basis_bps_mean": round(float(real.mean()), 3) if len(real) else None,
           "abort_cost_bps_mean": round(float(np.nanmean([r.abort_cost_bps for r in aborted])), 3)
           if aborted and np.isfinite([r.abort_cost_bps for r in aborted]).any() else None,
           "source": sorted({r.source for r in rounds})}
    if len(fr):
        out.update({"capture_fraction_mean": round(float(fr.mean()), 4),
                    "capture_fraction_median": round(float(np.median(fr)), 4),
                    "capture_fraction_p10": round(float(np.quantile(fr, 0.10)), 4),
                    # notional-weighted: a 4-slice round that fills 1 slice must not
                    # count as if it had captured the whole signal.
                    "capture_fraction_weighted": round(float(np.sum(real * np.sign(sig))
                                                             / np.sum(np.abs(sig))), 4)
                    if np.sum(np.abs(sig)) > 0 else None,
                    "frac_capture_below_half": round(float((fr < 0.5).mean()), 4)})
    else:
        out["capture_fraction_mean"] = None
    return out


def summarize(rounds: Sequence[RoundOutcome], mids: dict[str, MidSeries],
              entry_deadline_s: float, horizons_s: Sequence[float] = MARKOUT_HORIZONS_S) -> dict:
    """One dict with everything `findings.md` quotes."""
    fills = [f for r in rounds for f in r.fills]
    entry_fills = [f for r in rounds for f in r.fills]
    return {
        "rounds": {
            "n": len(rounds),
            "states": {s: sum(1 for r in rounds if r.state == s)
                       for s in sorted({r.state for r in rounds})},
            "n_slices": {k: sum(1 for r in rounds if r.n_slices == k)
                         for k in sorted({r.n_slices for r in rounds})},
            "p_fail": p_fail(rounds, entry_deadline_s),
            "p_fail_curve": p_fail_curve(rounds),
            "time_to_pair": time_to_pair(rounds),
            "capture_fraction": capture_fraction(rounds),
        },
        "markout": markout_curve(entry_fills, mids, horizons_s),
        "fills": {
            "n": len(fills),
            "by_venue": {v: sum(1 for f in fills if f.venue == v) for v in sorted({f.venue for f in fills})},
            "by_source": {s: sum(1 for f in fills if f.source == s)
                          for s in sorted({f.source for f in fills})},
        },
    }


def distance_of(fill_px: float, mid_at_signal: float, side: str) -> float:
    """Resting distance used to bucket a fill (>=0 = inside our own side)."""
    return distance_bps(fill_px, mid_at_signal, side)
