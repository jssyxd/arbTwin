"""P0-2  Venue mechanics: the things that liquidate you while the basis looks fine.

Three mechanisms, all of them documented behaviour of the venues rather than
alpha, must be hard gates before any leg is sent:

1. **Mark window.**  A venue that marks off a 5-10 minute mid will settle your
   position at a price that has nothing to do with the price you clicked.  Any
   resting order must live shorter than the mark window, and any position must
   be sized so the mark can move to it without a margin call.
2. **Mark cap / band.**  Some venues clamp the mark inside a band (e.g. a hard
   upper cap).  Approaching the cap, longs are structurally doomed: the mark
   cannot follow spot, so the liquidation ladder sits right on top of you.
3. **Liquidation-cluster concentration.**  A venue whose open interest dwarfs
   the book depth within 1% of mid is one aggressive taker away from a cascade.
   We cannot see the position ladder, so we use a proxy that *is* observable:

        cascade_proxy = OI_usd / depth_1pct_usd

   and refuse to add risk above a threshold, and refuse to place stops inside
   the band where the cascade would run.

Everything here is pure: it takes observations, returns a verdict with reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Book, GateVerdict, MarketStats, VenuePosition


@dataclass
class VenueMechanics:
    venue: str
    funding_interval_h: float = 8.0
    mark_window_s: int = 0            # 0 = venue marks off spot/index continuously
    mark_cap_px: float = 0.0          # absolute upper cap published by the venue (0 = none)
    mark_floor_px: float = 0.0
    taker_speed_bump_ms: int = 0
    post_only_speed_bump_ms: int = 0
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    oi_utilization_max: float = 0.25
    # minimum notional needed to walk 25 bps away from mid on EITHER side.
    # This replaces the OI/depth ratio as a gate: measured on live books,
    # OI/depth(1%) is ~1 for ETH (132M OI vs 115M depth) and ~40 for NVDA, so a
    # single threshold either excludes ETH or admits everything.  Move cost is
    # the quantity that actually matters, and it is observable.
    min_move_cost_usd: float = 50000.0
    cascade_proxy_monitor: float = 50.0
    mark_divergence_max_bps: float = 40.0
    min_depth_usd_1pct: float = 2000.0
    # how much of the quoted notional may sit inside one tick of the cascade band
    cascade_band_pct: float = 0.01


@dataclass
class MechanicsBook:
    venues: dict[str, VenueMechanics] = field(default_factory=dict)

    def get(self, venue: str) -> VenueMechanics:
        return self.venues.get(venue.lower(), VenueMechanics(venue=venue))

    def add(self, mech: VenueMechanics) -> None:
        self.venues[mech.venue.lower()] = mech

    def describe(self) -> str:
        return "\n".join(
            f"{m.venue:<8s} funding={m.funding_interval_h:>4.1f}h mark_window={m.mark_window_s:>4}s "
            f"cap={m.mark_cap_px or '-':>10} bump(taker/post)={m.taker_speed_bump_ms}/{m.post_only_speed_bump_ms}ms "
            f"oi_util_max={m.oi_utilization_max:.0%} min_move=${m.min_move_cost_usd:,.0f}"
            for m in self.venues.values()
        )


def build_mechanics(venues_cfg: dict) -> MechanicsBook:
    book = MechanicsBook()
    for name, raw in ((venues_cfg or {}).get("venues") or {}).items():
        mech = (raw or {}).get("mechanics") or {}
        book.add(VenueMechanics(
            venue=name,
            funding_interval_h=float(mech.get("funding_interval_h", 8.0)),
            mark_window_s=int(mech.get("mark_window_s", 0)),
            mark_cap_px=float(mech.get("mark_cap_px", 0.0) or 0.0),
            mark_floor_px=float(mech.get("mark_floor_px", 0.0) or 0.0),
            taker_speed_bump_ms=int(mech.get("taker_speed_bump_ms", 0)),
            post_only_speed_bump_ms=int(mech.get("post_only_speed_bump_ms", 0)),
            maker_fee_bps=float(mech.get("maker_fee_bps", 0.0)),
            taker_fee_bps=float(mech.get("taker_fee_bps", 0.0)),
            oi_utilization_max=float(mech.get("oi_utilization_max", 0.25)),
            min_move_cost_usd=float(mech.get("min_move_cost_usd", 50000.0)),
            cascade_proxy_monitor=float(mech.get("cascade_proxy_monitor", 50.0)),
            mark_divergence_max_bps=float(mech.get("mark_divergence_max_bps", 40.0)),
            min_depth_usd_1pct=float(mech.get("min_depth_usd_1pct", 2000.0)),
            cascade_band_pct=float(mech.get("cascade_band_pct", 0.01)),
        ))
    return book


def cascade_proxy(stats: MarketStats, book: Book) -> float:
    depth = book.depth_usd(100.0)
    if depth <= 0:
        return float("inf")
    return stats.oi_usd / depth


def evaluate_venue(mech: VenueMechanics, stats: MarketStats, book: Book,
                   position: VenuePosition | None = None,
                   limits=None) -> GateVerdict:
    """Gate one venue for one symbol.  Returns warn/block with explicit reasons."""
    reasons: list[str] = []
    metrics: dict = {}
    allow = True

    if not (book.bids and book.asks):
        return GateVerdict.blocked(f"{mech.venue}: empty book", venue=mech.venue)

    depth_1pct = book.depth_usd(100.0)
    metrics["depth_1pct_usd"] = round(depth_1pct, 2)
    if depth_1pct < mech.min_depth_usd_1pct:
        allow = False
        reasons.append(f"{mech.venue}: 1% depth ${depth_1pct:,.0f} < ${mech.min_depth_usd_1pct:,.0f}")

    div = stats.mark_divergence_bps
    metrics["mark_divergence_bps"] = round(div, 2)
    if div > mech.mark_divergence_max_bps:
        allow = False
        reasons.append(f"{mech.venue}: mark-index divergence {div:.1f}bps > {mech.mark_divergence_max_bps:.1f}bps")

    oi_util = stats.oi_utilization
    metrics["oi_utilization"] = round(oi_util, 4)
    if oi_util > mech.oi_utilization_max:
        allow = False
        reasons.append(f"{mech.venue}: OI utilisation {oi_util:.1%} > {mech.oi_utilization_max:.0%}")

    # OI/depth is reported, not gated: it is a diagnostic, not a trade veto
    proxy = cascade_proxy(stats, book)
    metrics["oi_depth_ratio"] = round(proxy, 2)
    if proxy > mech.cascade_proxy_monitor:
        metrics["oi_depth_flag"] = True

    move_up = book.notional_to_cross(25.0, "ask")
    move_down = book.notional_to_cross(25.0, "bid")
    metrics["move_25bps_cost_usd"] = round(max(move_up, move_down), 0)
    if max(move_up, move_down) < mech.min_move_cost_usd:
        allow = False
        reasons.append(
            f"{mech.venue}: ${max(move_up, move_down):,.0f} moves the book 25bps "
            f"(< ${mech.min_move_cost_usd:,.0f}) — thin reference"
        )

    if mech.mark_cap_px > 0 and stats.mark_price > 0:
        cap_dist = (mech.mark_cap_px - stats.mark_price) / stats.mark_price
        metrics["mark_cap_distance_pct"] = round(cap_dist, 4)
        if cap_dist <= 0:
            allow = False
            reasons.append(f"{mech.venue}: mark at/above venue cap ${mech.mark_cap_px:.4f}")
        elif cap_dist < 0.08:
            allow = False
            reasons.append(
                f"{mech.venue}: mark {cap_dist:.1%} below venue cap — longs structurally liquidatable"
            )

    if mech.mark_floor_px > 0 and stats.mark_price > 0:
        floor_dist = (stats.mark_price - mech.mark_floor_px) / stats.mark_price
        metrics["mark_floor_distance_pct"] = round(floor_dist, 4)
        if floor_dist < 0.08:
            allow = False
            reasons.append(f"{mech.venue}: mark {floor_dist:.1%} above venue floor — shorts structurally liquidatable")

    if position is not None:
        liq_dist = position.liq_distance_pct
        metrics["liq_distance_pct"] = None if liq_dist == float("inf") else round(liq_dist, 4)
        need = getattr(limits, "min_liq_distance_pct", 0.18)
        if liq_dist < need:
            allow = False
            reasons.append(f"{mech.venue}: liquidation only {liq_dist:.1%} away (< {need:.0%})")

    if mech.mark_window_s > 0:
        ttl = getattr(limits, "max_quote_ttl_s", 120)
        metrics["quote_ttl_s"] = ttl
        if ttl > mech.mark_window_s / 2:
            allow = False
            reasons.append(
                f"{mech.venue}: quote ttl {ttl}s > half the {mech.mark_window_s}s mark window "
                f"(will be marked away from your price)"
            )

    return GateVerdict(allow=allow, reasons=reasons, level="ok" if allow else "block", metrics=metrics)


def latency_cost_bps(sigma_1h_bps: float, latency_ms: float) -> float:
    """Expected adverse mark-to-market while a leg is in flight.

    Diffusion scaling: sigma over t seconds is sigma_hour * sqrt(t / 3600).
    Lighter's standard taker speed bump is 300 ms; that is 300 ms of unpriced
    risk and it belongs in the cost, not in the hope.
    """
    if latency_ms <= 0 or sigma_1h_bps <= 0:
        return 0.0
    return sigma_1h_bps * (latency_ms / 3_600_000.0) ** 0.5
