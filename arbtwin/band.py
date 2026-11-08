"""P1-4  Entry-band sizing that survives sigma -> 0 and sigma -> inf.

The LP lesson (zero-volatility leg => no IL, income is pure fee capture)
generalises to a two-venue vertical: the *width* you demand before entering
has to scale with realised volatility and with the fee tier, and it has to
degenerate gracefully at both ends.

    band_bps = max(floor_bps, k * sigma_basis_bps, fee_multiple * fee_bps)   (capped)

* sigma -> 0 (stablecoin pairs, SGOV-style bond tokens, weekend frozen books):
  the formula collapses to ~0, which would make you trade every 1-tick tick.
  The degenerate branch pins the band at `fee_bps * multiple` instead: with no
  volatility the only thing that can hurt you is cost, so demand cost coverage.
* sigma -> inf (a pre-IPO name gapping 10%): the cap keeps the band finite so
  the symbol stays in the universe instead of silently becoming untradable.

`exit_band_bps` is a fraction of the entry band: if you entered at 2 sigma you
should not need a full reversal to leave.
"""

from __future__ import annotations

from .config import BandParams


def entry_band_bps(sigma_basis_bps: float, fee_bps: float, params: BandParams | None = None) -> float:
    """How far the *basis* must move before we act.

    Three requirements, all necessary and each independently sufficient to widen
    the band:

    * `k * sigma_basis` — the move must be a real deviation, not noise;
    * `fee_multiple * fee` — it must pay for the round trip;
    * `floor` — a hard minimum so cross-venue tick noise never triggers a trade.

    Sigma here is the volatility of the **basis**, not of the price: a basis
    trade's risk is the spread between the two venues, and using price sigma
    (which is 10-20x larger) would produce bands nothing ever reaches.
    """
    p = params or BandParams()
    if sigma_basis_bps <= p.degenerate_sigma_bps:
        return min(max(p.floor_bps, max(fee_bps, 0.0) * p.degenerate_fee_multiple), p.cap_bps)
    raw = max(p.floor_bps, p.k_sigma * sigma_basis_bps, p.fee_multiple * max(fee_bps, 0.0))
    return min(raw, p.cap_bps)


def exit_band_bps(entry: float, params: BandParams | None = None) -> float:
    p = params or BandParams()
    return max(p.floor_bps * 0.5, entry * p.exit_ratio)


def sigma_1h_bps(returns: list[float]) -> float:
    """Population sigma of hourly fractional returns, expressed in bps."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    return (var ** 0.5) * 1e4


def returns_from_closes(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def max_position_usd(equity_usd: float, leverage_cap: float, margin_usage_pct: float,
                     depth_usd_1pct: float, depth_pct: float = 0.10,
                     hard_cap_usd: float = 3000.0) -> float:
    """Size a leg: the min of margin budget, book depth share and hard cap."""
    by_margin = max(equity_usd, 0.0) * leverage_cap * margin_usage_pct
    by_depth = max(depth_usd_1pct, 0.0) * depth_pct
    return max(0.0, min(by_margin, by_depth, hard_cap_usd))