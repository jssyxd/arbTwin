"""P1-5  FeeModel: cost is a function of (venue, order type, tier, incentive epoch).

The original project treated the maker fee as a module constant and derived a
hard hurdle as `fee * 2.0`.  That is wrong on two counts:

* fee is per venue **and** per order type (Lighter currently charges 0 for both,
  OKX charges ~2 bps maker / ~5 bps taker, and limit orders on some venues are
  free while IOC is not);
* incentive epochs (HIP-3 style volume programs, points, maker rebates) make
  the *effective* cost lower than the nominal fee for the duration of the
  epoch.  A hurdle calibrated on nominal fees leaves money on the table: you
  refuse to quote during the exact window where flow is subsidised.

`FeeSchedule.effective_bps(order_type)` is the number the gates must use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeeSchedule:
    venue: str
    maker_bps: float
    taker_bps: float
    # value of incentive accrual per notional, in bps (points, volume program).
    # Positive value *reduces* effective cost, negative value increases it
    # (e.g. a venue that charges a settlement/withdrawal amortisation).
    incentive_value_bps: float = 0.0
    # maker rebate paid to you (negative fee); use a positive number here.
    maker_rebate_bps: float = 0.0
    # per-order-type overrides, e.g. {"ioc": 6.0, "post_only": 0.0}
    overrides: dict = field(default_factory=dict)
    # round-trip non-fee friction: withdrawal / bridge / stablecoin conversion.
    settlement_cost_bps: float = 0.0

    def nominal_bps(self, order_type: str) -> float:
        key = order_type.lower()
        if key in self.overrides:
            return float(self.overrides[key])
        if key in ("post_only", "gtc", "limit", "maker"):
            return self.maker_bps - self.maker_rebate_bps
        if key in ("ioc", "market", "taker"):
            return self.taker_bps
        raise ValueError(f"unknown order type {order_type!r}")

    def effective_bps(self, order_type: str) -> float:
        return self.nominal_bps(order_type) - self.incentive_value_bps

    def hurdle_bps(self, order_type: str, multiple: float = 2.0) -> float:
        """Minimum gross edge required to bother trading this leg."""
        return max(self.effective_bps(order_type), 0.0) * multiple

    def describe(self) -> str:
        return (
            f"{self.venue:<8s} maker={self.maker_bps:.2f} taker={self.taker_bps:.2f} "
            f"rebate={self.maker_rebate_bps:.2f} incentive={self.incentive_value_bps:.2f} "
            f"settlement={self.settlement_cost_bps:.2f}"
        )


@dataclass
class FeeBook:
    """Both venues' schedules + the round-trip helpers the gates need."""

    okx: FeeSchedule
    lighter: FeeSchedule

    def by_venue(self, venue: str) -> FeeSchedule:
        venue = venue.lower()
        if venue == "okx":
            return self.okx
        if venue == "lighter":
            return self.lighter
        raise KeyError(venue)

    def round_trip_bps(self, lead_venue: str, hedge_venue: str,
                       lead_type: str = "post_only", hedge_type: str = "ioc",
                       close_type: str | None = None) -> float:
        """Cost of opening (lead + hedge) and later closing both legs.

        Closing is modelled symmetrically: the leg that entered as a maker exits
        as a taker if it must be forced, so we charge the more expensive of a
        maker/taker exit per venue.  This is deliberately conservative.
        """
        lead = self.by_venue(lead_venue)
        hedge = self.by_venue(hedge_venue)
        open_cost = lead.effective_bps(lead_type) + hedge.effective_bps(hedge_type)
        if close_type is None:
            # hedge_now: the exit may have to be taken -> charge the worse of the two
            close_cost = max(lead.effective_bps("ioc"), lead.effective_bps("post_only")) \
                + max(hedge.effective_bps("ioc"), hedge.effective_bps("post_only"))
        else:
            # dual_maker: exits leave through the passive queue
            close_cost = lead.effective_bps(close_type) + hedge.effective_bps(close_type)
        settlement = lead.settlement_cost_bps + hedge.settlement_cost_bps
        return open_cost + close_cost + settlement

    def describe(self) -> str:
        return "\n".join([self.okx.describe(), self.lighter.describe()])


def build_fee_book(venues_cfg: dict) -> FeeBook:
    """Build from config/venues.yaml.

    ```yaml
    venues:
      okx:
        fee: {maker_bps: 2.0, taker_bps: 5.0, overrides: {post_only: 2.0}}
      lighter:
        fee: {maker_bps: 0.0, taker_bps: 0.0, incentive_value_bps: 0.0}
    ```
    """
    venues = (venues_cfg or {}).get("venues") or {}

    def one(name: str) -> FeeSchedule:
        raw = ((venues.get(name) or {}).get("fee") or {})
        return FeeSchedule(
            venue=name,
            maker_bps=float(raw.get("maker_bps", 0.0)),
            taker_bps=float(raw.get("taker_bps", 0.0)),
            incentive_value_bps=float(raw.get("incentive_value_bps", 0.0)),
            maker_rebate_bps=float(raw.get("maker_rebate_bps", 0.0)),
            overrides=dict(raw.get("overrides") or {}),
            settlement_cost_bps=float(raw.get("settlement_cost_bps", 0.0)),
        )

    return FeeBook(okx=one("okx"), lighter=one("lighter"))


def fee_from_market_specs(okx_spec, lighter_spec, config: Optional[dict] = None,
                          incentive_value_bps: float = 0.0) -> FeeBook:
    """Override config fees with the live per-market fees published by venues."""
    config = config or {}
    return FeeBook(
        okx=FeeSchedule("okx", maker_bps=okx_spec.maker_fee_bps, taker_bps=okx_spec.taker_fee_bps,
                        incentive_value_bps=incentive_value_bps,
                        overrides={"post_only": okx_spec.maker_fee_bps}),
        lighter=FeeSchedule("lighter", maker_bps=lighter_spec.maker_fee_bps,
                            taker_bps=lighter_spec.taker_fee_bps,
                            incentive_value_bps=incentive_value_bps,
                            overrides={"post_only": lighter_spec.maker_fee_bps}),
    )