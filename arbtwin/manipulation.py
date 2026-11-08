"""P2-7  Manipulation cost: how much notional does it take to move this print?

The observable question behind every thin book is "can someone move the
reference the venue marks off, cheaper than my position is worth?".  We cannot
see the oracle, but we can see the book the venue publishes, so we price the
attack:

    move_cost_bps(bps, side) = notional required to walk `bps` away from mid

with two derived flags:

* `thin_reference`: moving the mark 25 bps costs less than `min_cost_usd`.
  On a book like that, a stop inside the band is a donation.
* `depth_asymmetry`: bid depth vs ask depth within 1%.  One-sided thinness is
  what makes a squeeze cheap — the aggressor only has to clear one side, and
  the forced liquidations supply the other.

Both flags widen the band and raise the latency charge instead of vetoing the
symbol; the veto belongs to `mechanics` (depth/OI), the pricing response
belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Book


@dataclass
class ManipulationProfile:
    symbol: str
    venue: str
    move_up_usd_25bps: float = 0.0
    move_down_usd_25bps: float = 0.0
    depth_bid_1pct_usd: float = 0.0
    depth_ask_1pct_usd: float = 0.0
    thin_reference: bool = False
    depth_asymmetry: float = 0.0        # |bid-ask| / max(bid,ask)
    band_multiplier: float = 1.0        # applied to the entry band
    latency_multiplier: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "move_up_usd_25bps": round(self.move_up_usd_25bps, 2),
            "move_down_usd_25bps": round(self.move_down_usd_25bps, 2),
            "depth_asymmetry": round(self.depth_asymmetry, 3),
            "thin_reference": self.thin_reference,
            "band_multiplier": round(self.band_multiplier, 2),
            "reasons": self.reasons,
        }


def profile(book: Book, symbol: str = "", venue: str = "",
            ref_bps: float = 25.0, min_cost_usd: float = 25_000.0) -> ManipulationProfile:
    up = book.notional_to_cross(ref_bps, "ask")
    down = book.notional_to_cross(ref_bps, "bid")
    bid_depth = book.depth_usd(100.0, "bid")
    ask_depth = book.depth_usd(100.0, "ask")
    prof = ManipulationProfile(
        symbol=symbol,
        venue=venue,
        move_up_usd_25bps=up,
        move_down_usd_25bps=down,
        depth_bid_1pct_usd=bid_depth,
        depth_ask_1pct_usd=ask_depth,
    )
    heaviest = max(up, down)
    if heaviest < min_cost_usd:
        prof.thin_reference = True
        prof.reasons.append(
            f"moving the book {ref_bps:.0f}bps costs only ${heaviest:,.0f} (< ${min_cost_usd:,.0f})"
        )
        # quadratic penalty: the cheaper the move, the wider we need to be
        prof.band_multiplier = min(4.0, (min_cost_usd / max(heaviest, 1.0)) ** 0.5)
        prof.latency_multiplier = min(3.0, prof.band_multiplier)

    denom = max(bid_depth, ask_depth, 1.0)
    prof.depth_asymmetry = abs(bid_depth - ask_depth) / denom
    if prof.depth_asymmetry > 0.5:
        prof.reasons.append(f"one-sided book: {prof.depth_asymmetry:.0%} depth asymmetry within 1%")
        prof.band_multiplier = max(prof.band_multiplier, 1.5)
    return prof


def manipulation_cost_bps(book: Book, notional_usd: float) -> float:
    """Inverse question for sizing: how far can `notional_usd` push this book?"""
    if book.mid <= 0 or notional_usd <= 0:
        return 0.0
    for side, levels in (("ask", book.asks), ("bid", book.bids)):
        filled = 0.0
        for px, sz in levels:
            filled += px * sz
            if filled >= notional_usd:
                return abs(px - book.mid) / book.mid * 1e4
    return float("inf")