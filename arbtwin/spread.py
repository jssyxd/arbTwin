"""P0-1  The OKX <-> Lighter premium matrix.

One row per symbol.  The math, in order:

1. normalise Lighter prices into OKX base units: `px / unit_ratio`
2. `basis_bps` = normalised Lighter mid vs OKX mid (this is what the eye sees)
3. `executable_edge_bps` = what you would actually capture crossing the book:
   sell the rich bid, buy the cheap ask, both normalised
4. subtract **fee**, **latency drift** (Lighter's 300 ms taker bump is real
   money) and **slippage** to get `net_edge_bps`
5. compare with the symbol's entry band (`arbtwin.band`) and emit an action

Sign convention: positive `basis_bps` = Lighter is rich -> long OKX / short
Lighter.  Direction is never inferred from the basis alone: it is inferred from
the executable edge, which can disagree with the basis when one side's book is
one tick wide and the other is 20 bps wide.
"""

from __future__ import annotations

from dataclasses import dataclass

from .band import entry_band_bps
from .fees import FeeBook
from .mechanics import latency_cost_bps
from .models import Book, ExecutionPlan, OrderRequest, SpreadRow


def compute_row(
    symbol: str,
    okx_book: Book,
    lighter_book: Book,
    unit_ratio: float,
    fees: FeeBook,
    sigma_1h_bps: float,
    band_params=None,
    sigma_basis_bps: float | None = None,
    latency_ms: int = 300,
    slippage_bps: float = 2.0,
    lead_venue: str = "lighter",
    hedge_venue: str = "okx",
    lead_type: str = "post_only",
    hedge_type: str = "ioc",
    funding_cost_bps: float = 0.0,
    band_override_bps: float = 0.0,
    now: float | None = None,
) -> SpreadRow:
    import time as _time

    ts = now or _time.time()
    if unit_ratio <= 0:
        return SpreadRow(symbol=symbol, ts=ts, unit_ratio=unit_ratio, okx_mid=okx_book.mid,
                         lighter_mid_norm=0.0, basis_bps=0.0, executable_edge_bps=0.0,
                         fee_bps=0.0, latency_cost_bps=0.0, slippage_bps=0.0,
                         net_edge_bps=0.0, entry_band_bps=0.0,
                         direction="flat", action="BLOCK",
                         reasons=["unit ratio not calibrated"])

    okx_mid = okx_book.mid
    l_mid = lighter_book.mid / unit_ratio if lighter_book.mid else 0.0
    if okx_mid <= 0 or l_mid <= 0:
        return SpreadRow(symbol=symbol, ts=ts, unit_ratio=unit_ratio, okx_mid=okx_mid,
                         lighter_mid_norm=l_mid, basis_bps=0.0, executable_edge_bps=0.0,
                         fee_bps=0.0, latency_cost_bps=0.0, slippage_bps=0.0,
                         net_edge_bps=0.0, entry_band_bps=0.0,
                         direction="flat", action="BLOCK", reasons=["empty book"])

    basis = (l_mid - okx_mid) / okx_mid * 1e4
    okx_bid, okx_ask = okx_book.bid, okx_book.ask
    l_bid_n, l_ask_n = lighter_book.bid / unit_ratio, lighter_book.ask / unit_ratio

    # crossing edge in the direction the basis points at
    if basis >= 0:  # lighter rich: sell lighter bid, buy okx ask
        direction = "long_okx_short_lighter"
        edge = l_bid_n - okx_ask
    else:           # lighter cheap: buy lighter ask, sell okx bid
        direction = "long_lighter_short_okx"
        edge = okx_bid - l_ask_n
    exec_edge_bps = edge / okx_mid * 1e4

    fee_bps = fees.round_trip_bps(lead_venue, hedge_venue, lead_type, hedge_type)
    lat_bps = latency_cost_bps(sigma_1h_bps, latency_ms)
    band = entry_band_bps(sigma_basis_bps if sigma_basis_bps is not None else sigma_1h_bps,
                          fee_bps, band_params)
    if band_override_bps > 0:
        # override is a GROSS basis trigger (what the operator sees on the matrix),
        # not a net-edge threshold: "enter when |basis| >= 12.7bps and net > 0"
        band = band_override_bps
    net = exec_edge_bps - fee_bps - lat_bps - slippage_bps - funding_cost_bps

    # passive alternative: quote both venues post-only.  No taker fee, no
    # latency drift, no slippage — the price of that is one-sided-fill risk, so
    # it needs its own (wider, sigma-scaled) band.
    maker_fee = fees.round_trip_bps(lead_venue, hedge_venue, "post_only", "post_only",
                                    close_type="post_only")
    # A dual-maker round trip crosses the half-spread twice (entry at our own side,
    # exit back through it).  Leaving it out makes the live gate 2-5 bps optimistic
    # and lets the engine enter where the screener has already said "below b*".
    spread_cost_bps = 0.0
    if okx_book.spread_bps < 1e4 and lighter_book.spread_bps < 1e4:
        spread_cost_bps = okx_book.spread_bps + lighter_book.spread_bps
    # The opportunity is the *size* of the dislocation, not its sign: wave mode
    # triggers on abs(basis) and then picks the legs from the sign.  Using the
    # signed basis here made every negative-basis dislocation (Lighter cheap ->
    # long Lighter / short OKX) look unprofitable, so half the distribution was
    # structurally untradeable.
    maker_net = abs(basis) - maker_fee - spread_cost_bps - funding_cost_bps
    maker_band = entry_band_bps(sigma_basis_bps if sigma_basis_bps is not None else sigma_1h_bps,
                                maker_fee, band_params)
    if band_override_bps > 0:
        maker_band = band_override_bps

    row = SpreadRow(
        symbol=symbol, ts=ts, unit_ratio=unit_ratio,
        okx_mid=okx_mid, lighter_mid_norm=l_mid,
        basis_bps=basis, executable_edge_bps=exec_edge_bps,
        fee_bps=fee_bps, latency_cost_bps=lat_bps, slippage_bps=slippage_bps,
        net_edge_bps=net, entry_band_bps=band,
        maker_fee_bps=maker_fee, maker_net_bps=maker_net, maker_band_bps=maker_band,
        spread_cost_bps=spread_cost_bps,
        okx_spread_bps=(okx_book.spread_bps if okx_book.spread_bps < 1e4 else 0.0),
        lt_spread_bps=(lighter_book.spread_bps if lighter_book.spread_bps < 1e4 else 0.0),
        direction=direction,
    )
    gross_triggered = abs(basis) >= band if band_override_bps > 0 else False
    if band_override_bps > 0:
        if gross_triggered and net > 0.5:
            row.action = "ENTER"
            row.reasons.append(f"|basis| {abs(basis):.1f}bps >= trigger {band:.1f}bps, "
                               f"hedge-now net {net:.1f}bps")
        elif gross_triggered and maker_net > 0.5:
            row.action = "MAKER"
            row.reasons.append(f"|basis| {abs(basis):.1f}bps >= trigger {band:.1f}bps, "
                               f"maker net {maker_net:.1f}bps")
        else:
            row.action = "WAIT"
            row.reasons.append(
                f"|basis| {abs(basis):.1f}bps vs trigger {band:.1f}bps "
                f"(hedge {net:+.1f} / maker {maker_net:+.1f} bps)")
    elif net >= band:
        row.action = "ENTER"
        row.reasons.append(f"hedge-now net {net:.1f}bps >= band {band:.1f}bps")
    elif maker_net >= maker_band:
        row.action = "MAKER"
        row.reasons.append(f"maker net {maker_net:.1f}bps >= band {maker_band:.1f}bps "
                           f"(hedge-now only {net:.1f}bps)")
    else:
        row.action = "WAIT"
        row.reasons.append(f"hedge-now net {net:.1f}bps / maker net {maker_net:.1f}bps "
                           f"below bands {band:.1f}/{maker_band:.1f}bps")
        if maker_net > 0 and maker_net < maker_band:
            row.reasons.append("basis covers passive fees but not a defensible band")
    return row


def build_plan(row: SpreadRow, okx_book: Book, lighter_book: Book, unit_ratio: float,
               notional_usd: float, max_unhedged_ms: int = 800,
               mode: str = "hedge_now") -> ExecutionPlan:
    """Turn an ENTER row into a concrete two-leg plan.

    Leg order is a venue-mechanics decision, not a style choice:

    * Lighter's *taker* path carries a speed bump (300 ms on Standard accounts)
      while post-only has no bump and no fee -> the Lighter leg leads as a
      post-only order;
    * OKX is the fast, liquid leg -> it hedges with an IOC once Lighter confirms.

    The watchdog (`max_unhedged_ms`) cancels the leading leg if the hedge cannot
    be sent in time; that bound is the entire risk budget of the trade.
    """
    if unit_ratio <= 0 or notional_usd <= 0 or not okx_book.mid or not lighter_book.mid:
        raise ValueError("cannot build a plan without a calibrated ratio, a size and two books")

    if row.direction == "long_okx_short_lighter":
        okx_side, l_side = "buy", "sell"
        okx_px = okx_book.ask
        l_px = lighter_book.bid
    else:
        okx_side, l_side = "sell", "buy"
        okx_px = okx_book.bid
        l_px = lighter_book.ask

    okx_qty = notional_usd / okx_px
    l_qty = okx_qty / unit_ratio

    if mode == "dual_maker":
        # both legs rest at the touch: no taker anywhere, cost = maker fees only
        leading = OrderRequest("lighter", row.symbol, l_side, l_qty, l_px,
                               order_type="limit", tif="post_only")
        hedge = OrderRequest("okx", row.symbol, okx_side, okx_qty, okx_px,
                             order_type="limit", tif="post_only")
        return ExecutionPlan(
            symbol=row.symbol, direction=row.direction, leading=leading, hedge=hedge,
            expected_net_bps=row.maker_net_bps, mode="dual_maker",
            rationale=(f"{row.direction} maker/maker @ {row.maker_net_bps:.1f}bps "
                       f"(band {row.maker_band_bps:.1f}bps, fee {row.maker_fee_bps:.1f}bps)"),
            max_unhedged_ms=max_unhedged_ms,
        )

    leading = OrderRequest("lighter", row.symbol, l_side, l_qty, l_px,
                           order_type="limit", tif="post_only")
    hedge = OrderRequest("okx", row.symbol, okx_side, okx_qty, okx_px,
                         order_type="limit", tif="ioc")
    return ExecutionPlan(
        symbol=row.symbol,
        direction=row.direction,
        leading=leading,
        hedge=hedge,
        expected_net_bps=row.net_edge_bps,
        mode="hedge_now",
        rationale=f"{row.direction} @ net {row.net_edge_bps:.1f}bps (band {row.entry_band_bps:.1f}bps)",
        max_unhedged_ms=max_unhedged_ms,
    )


@dataclass
class MatrixSummary:
    n: int = 0
    n_enter: int = 0
    best_symbol: str = ""
    best_net_bps: float = float("-inf")
    rows: list = None


def summarise(rows: list[SpreadRow]) -> MatrixSummary:
    s = MatrixSummary(n=len(rows), rows=rows)
    for r in rows:
        if r.action == "ENTER":
            s.n_enter += 1
        if r.net_edge_bps > s.best_net_bps:
            s.best_net_bps, s.best_symbol = r.net_edge_bps, r.symbol
    if not rows:
        s.best_net_bps = 0.0
    return s