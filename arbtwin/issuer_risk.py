"""P2-6  Issuer / venue-structure risk card.

The pattern to avoid is a tokenised claim whose only exit liquidity is you:
recursive collateral (the protocol's own token posted as collateral for a
borrowable asset), a supply cap that the same team keeps raising, an oracle
with a single feed, and incentives that exist purely to attract the exit.

Most of that is not observable by API, so the card mixes three inputs:

1. **Registry flags** (`config/risk.yaml -> registry`) — human judgement, in
   version control, one line per symbol, auditable.
2. **Live structural facts** from the venue: `market_deployer`, margin
   fractions, funding clamps, OI limit, listing age, whether the venue even
   publishes an index price.
3. **Concentration math**: OI utilisation and OI vs depth (shared with
   mechanics, but scored here as a *business* risk rather than a trade gate).

Any hard flag vetoes the symbol.  Soft flags only shrink size (`size_factor`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import MarketSpec, MarketStats


@dataclass
class RiskCard:
    symbol: str
    score: float = 0.0                 # 0 = clean, higher = worse
    size_factor: float = 1.0           # multiply position size by this
    allow: bool = True
    hard_flags: list[str] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 2),
            "size_factor": round(self.size_factor, 3),
            "allow": self.allow,
            "hard_flags": self.hard_flags,
            "soft_flags": self.soft_flags,
        }


HARD_REGISTRY_FLAGS = {
    "recursive_collateral",   # own token posted as collateral
    "single_oracle",          # one feed controls the mark
    "mutable_supply_cap",     # issuer can mint into the float at will
    "issuer_is_counterparty",  # issuer runs the venue that lends against it
    "incentives_are_exit_liquidity",
    "pre_ipo_claim",          # no reference market, price is a hope
    "no_index_price",
}

SOFT_REGISTRY_FLAGS = {
    "synthetic_spot",
    "thin_float",
    "recently_listed",
    "weekend_gap",
}


def build_card(symbol: str, specs: list[MarketSpec], stats: list[MarketStats],
               registry: dict | None = None) -> RiskCard:
    registry = registry or {}
    entry = registry.get(symbol) or {}
    flags = [str(f) for f in (entry.get("flags") or [])]
    card = RiskCard(symbol=symbol)

    for f in flags:
        if f in HARD_REGISTRY_FLAGS:
            card.hard_flags.append(f"registry:{f}")
        elif f in SOFT_REGISTRY_FLAGS:
            card.soft_flags.append(f"registry:{f}")
        else:
            card.soft_flags.append(f"registry:{f}")

    # live structural checks
    for spec in specs:
        if spec.venue == "lighter":
            if spec.initial_margin_frac < 0.05:
                card.soft_flags.append("initial margin < 5% (leverage > 20x, thin buffer)")
                card.size_factor *= 0.8
        if spec.venue == "lighter" and not spec.oi_limit_usd:
            card.soft_flags.append("lighter: no published OI limit")

    for st in stats:
        if st.index_price <= 0:
            card.hard_flags.append(f"{st.venue}: no index price published")
        util = st.oi_utilization
        if util > 0.5:
            card.hard_flags.append(f"{st.venue}: OI utilisation {util:.0%} of venue limit")
        elif util > 0.3:
            card.soft_flags.append(f"{st.venue}: OI utilisation {util:.0%} of venue limit")
            card.size_factor *= 0.7
        if abs(st.funding_apr_pct) > 300:
            card.soft_flags.append(f"{st.venue}: funding {st.funding_apr_pct:.0f}% APR — crowded side")
            card.size_factor *= 0.8
        if st.mark_divergence_bps > 100:
            card.soft_flags.append(f"{st.venue}: mark-index divergence {st.mark_divergence_bps:.0f}bps")

    card.score = 2.0 * len(card.hard_flags) + 1.0 * len(card.soft_flags)
    card.allow = not card.hard_flags
    card.size_factor = max(0.1, min(1.0, card.size_factor))
    if not card.allow:
        card.size_factor = 0.0
    return card
