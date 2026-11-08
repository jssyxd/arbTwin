"""Core data structures shared by every layer of arbTwin.

Unit convention (the single most important thing in this repo)
--------------------------------------------------------------
Every venue quotes a price per *its own* contract unit.  arbTwin converts them
to one canonical unit, defined as **1 base unit of the OKX instrument**
(1 ETH, 1 AAPL, 1000 SHIB ...):

    canonical_price(lighter) = lighter_price / unit_ratio

`unit_ratio` = how many Lighter base units equal one OKX base unit.  It is
calibrated from history (see `arbtwin.calibration`) and *never* hardcoded,
because venues re-denominate contracts without notice (see README, ANTHROPIC).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

OKX = "okx"
LIGHTER = "lighter"


# --------------------------------------------------------------------------- #
# market / book
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketSpec:
    """Static contract specification of one instrument on one venue."""

    venue: str
    symbol: str                # canonical symbol, e.g. "AAPL" or "1000SHIB"
    market_ref: str            # OKX instId, or Lighter market_id as str
    price_decimals: int
    size_decimals: int
    min_base: float
    min_quote: float
    contract_multiplier: float = 1.0    # base units per contract (OKX ctVal * ctMult)
    initial_margin_frac: float = 0.05   # 1 / max leverage
    maintenance_margin_frac: float = 0.012
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    oi_limit_usd: float = 0.0
    max_leverage: float = 20.0
    status: str = "active"

    @property
    def tradable(self) -> bool:
        return self.status in ("active", "live", "open")

    def lot_size(self, qty_base: float) -> float:
        """Round a base-unit quantity down to the venue lot size."""
        step = 10.0 ** (-self.size_decimals)
        return math.floor(qty_base / step + 1e-12) * step

    def meets_minimum(self, qty_base: float, px: float) -> bool:
        return qty_base >= self.min_base * self.contract_multiplier - 1e-12 and qty_base * px >= self.min_quote - 1e-9

    def contracts(self, qty_base: float) -> float:
        return qty_base / self.contract_multiplier


@dataclass
class Book:
    """Top-of-book snapshot.  bids/asks are (price, base size) sorted best first."""

    venue: str
    symbol: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.bids = sorted(self.bids, key=lambda x: -x[0])
        self.asks = sorted(self.asks, key=lambda x: x[0])

    @property
    def bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def mid(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.bids[0][0] + self.asks[0][0]) / 2.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return (self.asks[0][0] - self.bids[0][0]) / m * 1e4 if m else float("inf")

    def valid(self, max_age_s: float = 5.0) -> bool:
        return bool(self.bids and self.asks) and (time.time() - self.ts) <= max_age_s

    def depth_usd(self, bps: float, side: Optional[str] = None) -> float:
        """Notional available within `bps` of mid, on one side or both."""
        m = self.mid
        if not m:
            return 0.0
        lo, hi = m * (1 - bps / 1e4), m * (1 + bps / 1e4)
        total = 0.0
        if side in (None, "bid"):
            total += sum(px * sz for px, sz in self.bids if px >= lo)
        if side in (None, "ask"):
            total += sum(px * sz for px, sz in self.asks if px <= hi)
        return total

    def covered_bps(self, side: Optional[str] = None) -> float:
        """How far from mid the snapshot actually reaches, on the thinner side.

        A 400-level snapshot of BTC reaches ~5 bps from mid (tick 0.013 bps);
        a 400-level snapshot of AAPL reaches ~220 bps (tick 0.29 bps).  Any
        "depth within X bps" number is only meaningful up to this distance.
        """
        m = self.mid
        if not m:
            return 0.0
        out = []
        if side in (None, "bid") and self.bids:
            out.append((m - self.bids[-1][0]) / m * 1e4)
        if side in (None, "ask") and self.asks:
            out.append((self.asks[-1][0] - m) / m * 1e4)
        return min(out) if out else 0.0

    def depth_covered(self, bps: float, side: Optional[str] = None) -> tuple[float, float, bool]:
        """(notional, covered_bps, complete) for the band `bps` around mid.

        `complete=False` means the book ends before the band does, so the
        notional is a lower bound.  Gates must never present a lower bound as
        if it were the measured depth (that is how a BTC book "loses" 95% of
        its liquidity to a level cap).
        """
        covered = min(bps, self.covered_bps(side))
        usd = self.depth_usd(covered, side)
        complete = self.covered_bps(side) >= bps
        return usd, covered, complete

    def depth_density_usd_per_bps(self, side: Optional[str] = None,
                                 band_bps: float = 100.0) -> tuple[float, float, bool]:
        """Notional per bp inside the band — scale-free liquidity measure."""
        usd, covered, complete = self.depth_covered(band_bps, side)
        if covered <= 0:
            return 0.0, 0.0, False
        return usd / covered, covered, complete

    def notional_to_cross(self, bps: float, side: str) -> float:
        """Notional needed to walk the book `bps` away from mid on one side."""
        m = self.mid
        if not m:
            return 0.0
        if side == "ask":  # buying lifts asks
            limit = m * (1 + bps / 1e4)
            return sum(px * sz for px, sz in self.asks if px <= limit)
        limit = m * (1 - bps / 1e4)
        return sum(px * sz for px, sz in self.bids if px >= limit)


@dataclass
class MarketStats:
    venue: str
    symbol: str
    mark_price: float
    index_price: float
    last_price: float = 0.0
    mid_price: float = 0.0
    oi_usd: float = 0.0
    oi_limit_usd: float = 0.0
    funding_rate: float = 0.0          # per funding interval, as a fraction
    funding_interval_h: float = 8.0
    premium: float = 0.0               # (mark - index) / index as reported by venue
    ts: float = field(default_factory=time.time)

    @property
    def funding_apr_pct(self) -> float:
        periods = 24.0 / self.funding_interval_h * 365.0
        return self.funding_rate * periods * 100.0

    @property
    def mark_divergence_bps(self) -> float:
        if not self.index_price:
            return 0.0
        return abs(self.mark_price - self.index_price) / self.index_price * 1e4

    @property
    def oi_utilization(self) -> float:
        if not self.oi_limit_usd:
            return 0.0
        return self.oi_usd / self.oi_limit_usd


@dataclass
class VenuePosition:
    venue: str
    symbol: str
    qty_base: float            # signed, in canonical base units
    avg_entry: float = 0.0
    liquidation_price: float = 0.0
    notional_usd: float = 0.0
    margin_usd: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def liq_distance_pct(self) -> float:
        if not self.liquidation_price or not self.avg_entry:
            return float("inf")
        return abs(self.avg_entry - self.liquidation_price) / self.avg_entry


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
@dataclass
class GateVerdict:
    allow: bool
    reasons: list[str] = field(default_factory=list)
    level: str = "ok"          # ok | warn | block
    metrics: dict = field(default_factory=dict)

    @classmethod
    def blocked(cls, *reasons: str, **metrics) -> "GateVerdict":
        return cls(allow=False, reasons=list(reasons), level="block", metrics=metrics)

    def merge(self, other: "GateVerdict") -> "GateVerdict":
        allow = self.allow and other.allow
        level = "block" if not allow else ("warn" if "warn" in (self.level, other.level) else "ok")
        return GateVerdict(allow, self.reasons + other.reasons, level, {**self.metrics, **other.metrics})


@dataclass
class SpreadRow:
    """One row of the OKX <-> Lighter premium matrix."""

    symbol: str
    ts: float
    unit_ratio: float
    okx_mid: float
    lighter_mid_norm: float
    basis_bps: float                 # normalized lighter mid vs okx mid
    executable_edge_bps: float       # cross the spread, before costs
    fee_bps: float
    latency_cost_bps: float
    slippage_bps: float
    net_edge_bps: float
    entry_band_bps: float
    # passive (maker-on-both-venues) alternative: no taker, no latency drift,
    # but you own the one-sided-fill risk instead of paying for the hedge
    maker_fee_bps: float = 0.0
    maker_net_bps: float = 0.0
    maker_band_bps: float = 0.0
    direction: str = "flat"          # long_lighter_short_okx | long_okx_short_lighter | flat
    action: str = "WAIT"             # ENTER (hedge now) | MAKER (post both) | WAIT | BLOCK
    reasons: list[str] = field(default_factory=list)


@dataclass
class OrderRequest:
    venue: str
    symbol: str
    side: str                        # buy | sell
    qty_base: float
    px: float
    order_type: str = "limit"        # limit | market
    tif: str = "post_only"           # post_only | gtc | ioc
    reduce_only: bool = False
    client_id: str = ""

    @property
    def notional_usd(self) -> float:
        return self.qty_base * self.px


@dataclass
class Fill:
    venue: str
    symbol: str
    side: str
    qty_base: float
    px: float
    fee_bps: float = 0.0
    ts: float = field(default_factory=time.time)
    order_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """A two-leg plan.  Exactly one leg is the *leading* leg."""

    symbol: str
    direction: str
    leading: OrderRequest
    hedge: OrderRequest
    expected_net_bps: float
    mode: str = "hedge_now"          # hedge_now | dual_maker
    rationale: str = ""
    created_at: float = field(default_factory=time.time)
    max_unhedged_ms: int = 800


def annualize_funding(rate: float, interval_h: float) -> float:
    """Funding rate per interval -> annualised percent (positive = longs pay)."""
    if not interval_h:
        return 0.0
    return rate * (24.0 / interval_h) * 365.0 * 100.0