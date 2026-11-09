"""Metrics for the validation harness.

All metrics operate on DataFrames of fill events, book snapshots, or trade
ticks, with optional grouping by symbol, venue, or time window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class MetricResult:
    name: str
    value: float
    unit: str = ""
    description: str = ""
    sub_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"name": self.name, "value": self.value}
        if self.unit:
            d["unit"] = self.unit
        if self.description:
            d["description"] = self.description
        if self.sub_metrics:
            d["sub"] = self.sub_metrics
        return d


# ── p_fill ──────────────────────────────────────────────────────────────────


def compute_p_fill(
    fill_events: pd.DataFrame,
    quote_events: pd.DataFrame,
    distance_bps: float = 0.0,
    horizon_s: float = 60.0,
) -> MetricResult:
    """Probability of fill within `horizon_s` for orders at `distance_bps` from mid.

    `distance_bps = 0` means at the touch (aggressive side of the book).
    Positive values mean deeper (further from counter-side touch).

    `fill_events` columns: venue, symbol, recv_ts, px, sz, side
    `quote_events` columns: venue, symbol, recv_ts, bid_px, ask_px
    """
    if fill_events.empty:
        return MetricResult("p_fill", 0.0, "fraction", "no fill data; p_fill cannot be estimated",
                            {"distance_bps": distance_bps, "horizon_s": horizon_s})

    if "recv_ts" not in fill_events.columns:
        return MetricResult("p_fill", -1.0, "fraction", "missing recv_ts column in fill_events")

    n_quotes = len(quote_events)
    if n_quotes == 0:
        return MetricResult("p_fill", 0.0, "fraction", "no quote data",
                            {"distance_bps": distance_bps, "horizon_s": horizon_s})

    # For each quote, check if a fill occurred within horizon_s at distance_bps
    matched = 0
    total = 0

    fill_ts = fill_events["recv_ts"].values
    fill_px = fill_events["px"].values

    for _, q in quote_events.iterrows():
        q_ts = q["recv_ts"]
        mid = (q["bid_px"] + q["ask_px"]) / 2.0
        if mid <= 0:
            continue
        buy_px = mid * (1 - distance_bps / 1e4)  # buy limit at this distance from mid
        sell_px = mid * (1 + distance_bps / 1e4)
        total += 1

        mask = (fill_ts >= q_ts) & (fill_ts <= q_ts + horizon_s * 1000)
        if mask.any():
            fills = fill_px[mask]
            # A fill at or below our buy px, or at or above our sell px counts
            if ((fills <= buy_px).any()) or ((fills >= sell_px).any()):
                matched += 1

    p = matched / max(total, 1)
    return MetricResult(
        "p_fill",
        round(p, 4),
        "fraction",
        f"P(fill at {distance_bps}bps within {horizon_s}s)",
        {"distance_bps": distance_bps, "horizon_s": horizon_s, "n_opportunities": total, "n_filled": matched},
    )


def compute_p_fill_by_level(
    fills: pd.DataFrame,
    l2_snapshots: pd.DataFrame,
    horizon_s: float = 60.0,
    max_levels: int = 5,
) -> MetricResult:
    """p_fill broken down by book level (1=touch, 2=second level, …).

    Uses L2 snapshots to determine how far from mid each level sits.
    Returns per-level fill probability within horizon_s.
    """
    if fills.empty or l2_snapshots.empty:
        return MetricResult("p_fill_by_level", 0.0, "", "insufficient L2 or fill data")

    levels: dict[int, list[float]] = {i: [] for i in range(1, max_levels + 1)}
    fill_ts = fills["recv_ts"].values
    fill_px = fills["px"].values
    total = 0

    for _, snap in l2_snapshots.iterrows():
        s_ts = snap["recv_ts"]
        bids = snap.get("bids", [])
        asks = snap.get("asks", [])
        if not bids or not asks:
            continue
        total += 1

        mask = (fill_ts >= s_ts) & (fill_ts <= s_ts + horizon_s * 1000)
        trade_fills = fill_px[mask] if mask.any() else np.array([])

        for level_idx in range(min(max_levels, len(bids))):
            level = level_idx + 1
            bid_px = bids[level_idx][0] if isinstance(bids[level_idx], (tuple, list)) else bids[level_idx]["px"]
            if trade_fills.size > 0 and (trade_fills <= bid_px).any():
                levels[level].append(1.0)
            else:
                levels[level].append(0.0)

    sub = {}
    for lv in range(1, max_levels + 1):
        vals = levels[lv]
        sub[f"level_{lv}"] = round(np.mean(vals), 4) if vals else 0.0

    avg = np.mean([v for v in sub.values()]) if sub else 0.0
    return MetricResult("p_fill_by_level", round(avg, 4), "fraction", f"per-level p_fill, {max_levels} levels", sub)


# ── time-to-pair ───────────────────────────────────────────────────────────


def compute_time_to_pair(
    fill_events: pd.DataFrame,
    symbol: str = "",
) -> MetricResult:
    """Distribution of time between first-leg fill and second-leg fill.

    Pairs fills by matching venue=okx and venue=lighter within a rolling window.
    Returns median, p25, p75 in seconds.
    """
    if fill_events.empty or "venue" not in fill_events.columns:
        return MetricResult("time_to_pair", -1.0, "s", "no venue-aware fill data")

    df = fill_events.sort_values("recv_ts").copy()
    okx_fills = df[df["venue"] == "okx"]
    lt_fills = df[df["venue"] == "lighter"]

    if okx_fills.empty or lt_fills.empty:
        return MetricResult("time_to_pair", -1.0, "s", "missing one-venue fill data")

    okx_ts = okx_fills["recv_ts"].values
    lt_ts = lt_fills["recv_ts"].values

    # Simple pairing: for each okx fill find the nearest lighter fill
    deltas = []
    for ot in okx_ts[: min(len(okx_ts), 5000)]:
        idx = np.searchsorted(lt_ts, ot)
        candidates = []
        if idx < len(lt_ts):
            candidates.append(abs(lt_ts[idx] - ot))
        if idx > 0:
            candidates.append(abs(lt_ts[idx - 1] - ot))
        if candidates:
            deltas.append(min(candidates) / 1000.0)

    if not deltas:
        return MetricResult("time_to_pair", -1.0, "s", "no pairs found")

    deltas_arr = np.array(deltas)
    sub = {
        "median_s": round(float(np.median(deltas_arr)), 3),
        "p25_s": round(float(np.percentile(deltas_arr, 25)), 3),
        "p75_s": round(float(np.percentile(deltas_arr, 75)), 3),
        "mean_s": round(float(np.mean(deltas_arr)), 3),
        "n_pairs": len(deltas),
    }
    return MetricResult("time_to_pair", sub["median_s"], "s", "time between first and second leg fill", sub)


# ── p_fail ──────────────────────────────────────────────────────────────────


def compute_p_fail(
    fill_events: pd.DataFrame,
    entry_deadline_s: float = 5400.0,
    symbol: str = "",
) -> MetricResult:
    """Probability that the first leg fills but the second does not within entry_deadline_s.

    Uses the same venue-pairing logic as time_to_pair.
    """
    if fill_events.empty or "venue" not in fill_events.columns:
        return MetricResult("p_fail", -1.0, "fraction", "no venue-aware fill data")

    df = fill_events.sort_values("recv_ts").copy()
    okx_fills = df[df["venue"] == "okx"]
    lt_fills = df[df["venue"] == "lighter"]

    if okx_fills.empty or lt_fills.empty:
        return MetricResult("p_fail", -1.0, "fraction", "missing one-venue fill data")

    okx_ts = okx_fills["recv_ts"].values
    lt_ts = lt_fills["recv_ts"].values
    deadline_ms = entry_deadline_s * 1000

    paired = 0
    unpaired = 0
    for ot in okx_ts[: min(len(okx_ts), 5000)]:
        # Is there a lighter fill within deadline?
        nearest_idx = np.searchsorted(lt_ts, ot)
        if nearest_idx < len(lt_ts) and lt_ts[nearest_idx] - ot <= deadline_ms:
            paired += 1
        elif nearest_idx > 0 and ot - lt_ts[nearest_idx - 1] <= deadline_ms:
            # lighter fill slightly before (both legs fill around same time)
            paired += 1
        else:
            unpaired += 1

    total = paired + unpaired
    p = unpaired / max(total, 1)
    return MetricResult(
        "p_fail",
        round(p, 4),
        "fraction",
        f"P(unpaired within {entry_deadline_s}s)",
        {"deadline_s": entry_deadline_s, "paired": paired, "unpaired": unpaired, "total": total},
    )


# ── markout ─────────────────────────────────────────────────────────────────


def compute_markout(
    fill_events: pd.DataFrame,
    quote_events: pd.DataFrame,
    horizons_s: list[float] | None = None,
) -> MetricResult:
    """Markout: mid-price change after fill at 1min / 5min / 30min.

    Positive markout = adverse (price moves against the fill).
    Negative markout = favourable (price moves with the fill).

    Returns markout in bps at each horizon.
    """
    if horizons_s is None:
        horizons_s = [60.0, 300.0, 1800.0]

    if fill_events.empty or quote_events.empty:
        return MetricResult("markout", 0.0, "bps", "insufficient data for markout")

    fill_ts = fill_events["recv_ts"].values
    fill_px = fill_events["px"].values
    fill_side = fill_events.get("side", pd.Series(["buy"] * len(fill_events)))

    quote_ts = quote_events["recv_ts"].values
    quote_mid = ((quote_events["bid_px"].values + quote_events["ask_px"].values) / 2.0)

    sub = {}
    for horizon in horizons_s:
        markouts = []
        horizon_ms = horizon * 1000
        for i in range(min(len(fill_ts), 500)):
            ft = fill_ts[i]
            fp = fill_px[i]
            if fp <= 0:
                continue
            # Find nearest quote at ft + horizon
            target_ts = ft + horizon_ms
            idx = np.searchsorted(quote_ts, target_ts)
            if idx >= len(quote_ts):
                continue
            future_mid = quote_mid[idx]
            if future_mid <= 0:
                continue
            side = fill_side.iloc[i] if hasattr(fill_side, 'iloc') else fill_side[i] if isinstance(fill_side, (list, np.ndarray)) else "buy"
            move_bps = (future_mid - fp) / fp * 1e4
            if side == "sell":
                move_bps = -move_bps
            markouts.append(move_bps)

        key = f"{int(horizon // 60)}min" if horizon >= 60 else f"{int(horizon)}s"
        sub[key] = round(float(np.mean(markouts)), 2) if markouts else 0.0

    overall = sub.get("1min", 0.0)
    return MetricResult("markout", overall, "bps", "adverse selection after fill (positive = adverse)", sub)


# ── capture_bps ─────────────────────────────────────────────────────────────


def compute_capture_bps(
    fill_events: pd.DataFrame,
    quote_events: pd.DataFrame,
    fee_bps: float = 4.0,
) -> MetricResult:
    """Estimated basis capture in bps: (entry_basis - exit_basis) net of fees.

    Requires paired fills (both venues). [INFERENCE] from mechanics-rounds:
    modelled as: capture = entry_basis - exit_basis - fee_bps, where
    entry/exit basis are derived from fill prices on each venue.
    """
    if fill_events.empty or quote_events.empty:
        return MetricResult("capture_bps", 0.0, "bps", "insufficient data for capture estimate")

    okx = fill_events[fill_events["venue"] == "okx"]
    lt = fill_events[fill_events["venue"] == "lighter"]

    if okx.empty or lt.empty:
        return MetricResult("capture_bps", 0.0, "bps", "missing one-venue data")

    # Use mean mid as proxy for basis at fill time
    quote_mid = ((quote_events["bid_px"].values + quote_events["ask_px"].values) / 2.0)
    avg_mid = float(np.mean(quote_mid)) if len(quote_mid) > 0 else 0.0

    okx_px = float(okx["px"].mean())
    lt_px = float(lt["px"].mean())

    if okx_px <= 0:
        return MetricResult("capture_bps", 0.0, "bps", "invalid OKX price")

    entry_basis = (lt_px - okx_px) / okx_px * 1e4
    # Assume exit at mid
    exit_basis = 0.0
    gross = abs(entry_basis - exit_basis)
    net = gross - fee_bps

    return MetricResult(
        "capture_bps",
        round(net, 2),
        "bps",
        "estimated net basis capture after fees",
        {"gross_bps": round(gross, 2), "fee_bps": fee_bps, "entry_basis_bps": round(entry_basis, 2)},
    )


# ── abort_cost_bps ─────────────────────────────────────────────────────────


def compute_abort_cost(
    fill_events: pd.DataFrame,
    quote_events: pd.DataFrame,
) -> MetricResult:
    """Estimated cost of aborted rounds: spread + adverse move on unwind.

    Per mechanics-rounds finding: abort rounds had no PnL visibility, so this
    estimates the unwind cost as half-spread + adverse drift from fill to unwind.
    """
    if fill_events.empty or quote_events.empty:
        return MetricResult("abort_cost_bps", 0.0, "bps", "insufficient data for abort cost")

    quote_mid = ((quote_events["bid_px"].values + quote_events["ask_px"].values) / 2.0)
    quote_spread = ((quote_events["ask_px"].values - quote_events["bid_px"].values) / quote_mid * 1e4)
    half_spread = float(np.nanmean(quote_spread) / 2.0) if len(quote_spread) > 0 else 5.0

    # Conservative: unwind costs half-spread * 2 (one on each side) + 1 bp slippage
    cost = half_spread * 2.0 + 1.0
    return MetricResult("abort_cost_bps", round(cost, 2), "bps", "estimated abort-unwind cost per leg",
                        {"half_spread_bps": round(half_spread, 2), "slippage_bps": 1.0})


# ── escalation_rate ─────────────────────────────────────────────────────────


def compute_escalation_rate(
    order_events: pd.DataFrame,
) -> MetricResult:
    """Fraction of exit or abort paths that required IOC escalation.

    Escalation means the passive exit deadline expired and a marketable IOC
    was fired instead — a direct cost event.
    """
    if order_events.empty:
        return MetricResult("escalation_rate", 0.0, "fraction", "no order data")

    if "tif" not in order_events.columns:
        return MetricResult("escalation_rate", -1.0, "fraction", "missing tif column")

    ioc = (order_events["tif"] == "ioc").sum()
    total = len(order_events)

    rate = ioc / max(total, 1)
    return MetricResult(
        "escalation_rate",
        round(rate, 4),
        "fraction",
        "fraction of orders that were IOC escalation",
        {"n_ioc": int(ioc), "n_total": int(total)},
    )


# ── All metrics ────────────────────────────────────────────────────────────


def compute_all_metrics(
    fills: pd.DataFrame,
    quotes: pd.DataFrame,
    l2_data: Optional[pd.DataFrame] = None,
    orders: Optional[pd.DataFrame] = None,
    entry_deadline_s: float = 5400.0,
    fee_bps: float = 4.0,
) -> list[MetricResult]:
    """Compute all available metrics on the given DataFrames.

    Returns a list of MetricResult objects.  Metrics that need data absent from
    the input DataFrames will return placeholder results (value = -1 or 0).
    """
    results: list[MetricResult] = []

    results.append(compute_p_fill(fills, quotes, distance_bps=0.0, horizon_s=60.0))
    results.append(compute_p_fill(fills, quotes, distance_bps=5.0, horizon_s=60.0))

    if l2_data is not None and not l2_data.empty:
        results.append(compute_p_fill_by_level(fills, l2_data))

    results.append(compute_time_to_pair(fills))
    results.append(compute_p_fail(fills, entry_deadline_s=entry_deadline_s))
    results.append(compute_markout(fills, quotes))
    results.append(compute_capture_bps(fills, quotes, fee_bps=fee_bps))
    results.append(compute_abort_cost(fills, quotes))

    if orders is not None and not orders.empty:
        results.append(compute_escalation_rate(orders))

    return results