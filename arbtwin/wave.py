"""Wave round state machine: one round at a time, limit-only, both legs to flat.

The operator's rule, in their words: *"一轮交易出清完毕两腿开仓完毕和平仓完毕
才开始下一轮；一腿先完成建仓就等另一腿；平仓时一腿按触发时点价出清，等另一腿出清"*.

That is a **single-slot state machine per symbol**, and it is deliberately not the
same thing as "fire and forget two orders":

    FLAT ──( |basis| >= entry_p95 )──► ENTRY_QUOTING ──(1 fills)──► ENTRY_PARTIAL
      ▲                                     │                            │
      │                          (basis back < exit_p50)        (other leg fills)
      │                                     ▼                            ▼
    ABORT_UNWIND ◄────────────────── cancel + passive unwind        HEDGED
      │                                                                  │
      │                                     ( |basis| <= exit_p50  or max_hold )
      │                                                                  ▼
      └─────────────────(both flat)──────────── EXIT_PARTIAL ◄──── EXIT_QUOTING
                                                    │  (1 fills)
                                                    └── wait / re-peg the other leg

Why the states matter more than the thresholds
----------------------------------------------
* A round owns **exactly one slot per symbol**. While a round is in any state but
  FLAT/ABORT_UNWIND, no new entry may be quoted for that symbol — otherwise two
  rounds overlap and the "closed pair" PnL becomes meaningless.
* `ENTRY_PARTIAL` is the only place where the book is one-sided: the filled leg is
  naked. It is bounded by `entry_deadline_s` *and* by the basis reverting (which
  means the dislocation we were paid to provide liquidity into is gone).
* Exits freeze their price at trigger (`freeze_px_on_trigger`) so the exit is a
  passive quote at a known basis, not a chase. Freezing has a cost: the frozen
  price can go stale, which is why exits carry an explicit deadline after which
  the only allowed escalation is one flagged IOC (cost is the price of survival).

Nothing here sends orders; `decide()` returns intents and the engine executes them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .models import Book, OrderRequest


class RoundState(str, Enum):
    FLAT = "flat"
    ENTRY_QUOTING = "entry_quoting"
    ENTRY_PARTIAL = "entry_partial"
    HEDGED = "hedged"
    EXIT_QUOTING = "exit_quoting"
    EXIT_PARTIAL = "exit_partial"
    ABORT_UNWIND = "abort_unwind"


@dataclass
class WaveParams:
    """Thresholds are *gross basis* numbers measured from history (p95 / p50)."""

    entry_bps: float = 14.0            # p95 of the 14d basis distribution
    exit_bps: float = 4.5              # p50 of the same distribution
    hysteresis: float = 0.75           # cancel entry quotes below entry*hysteresis
    requote_after_s: float = 5.0
    entry_deadline_s: float = 5400.0   # 90 min to complete the pair, else unwind
    exit_deadline_s: float = 3600.0    # 60 min of passive exit, then one IOC
    max_hold_h: float = 4.0
    freeze_px_on_trigger: bool = True
    # 0.5 bps was noise: with 4 bps of fees and 2-8 bps of half-spread a
    # "half a basis point" of net edge cannot pay for a single bad fill.
    min_edge_bps: float = 2.0
    adverse_basis_stop_bps: float = 25.0
    # ENTRY_PARTIAL is the only state holding a naked leg.  `limits.max_unhedged_*
    # existed in config but was never read; enforce it here.
    max_unhedged_notional_usd: float = 150.0
    adverse_move_stop_bps: float = 60.0


@dataclass
class Leg:
    venue: str
    side: str                  # buy | sell
    qty_base: float
    px: float = 0.0
    filled_qty: float = 0.0
    filled_px: float = 0.0

    @property
    def is_filled(self) -> bool:
        return self.filled_qty >= self.qty_base - 1e-12

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty_base - self.filled_qty)


@dataclass
class Round:
    symbol: str
    params: WaveParams
    round_id: int
    state: RoundState = RoundState.FLAT
    direction: str = "flat"            # long_okx_short_lighter | long_lighter_short_okx
    entry_legs: list[Leg] = field(default_factory=list)
    exit_legs: list[Leg] = field(default_factory=list)
    entry_ts: float = 0.0
    entry_basis_bps: float = 0.0
    entry_state_ts: float = 0.0
    exit_ts: float = 0.0
    exit_basis_bps: float = 0.0
    frozen: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    escalation_used: bool = False

    # ------------------------------------------------------------------ helpers
    @property
    def entry_age_s(self) -> float:
        return time.time() - self.entry_state_ts if self.entry_state_ts else 0.0

    @property
    def hold_h(self) -> float:
        return (time.time() - self.entry_ts) / 3600.0 if self.entry_ts else 0.0

    def filled_venues(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for leg in self.entry_legs:
            if leg.filled_qty > 0:
                out[leg.venue] = (leg.filled_qty, leg.filled_px)
        return out

    def naked_legs(self) -> list[Leg]:
        filled = [l for l in self.entry_legs if l.filled_qty > 0]
        empty = [l for l in self.entry_legs if l.filled_qty <= 0]
        return [] if (not filled or not empty) else filled + empty

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "round_id": self.round_id, "state": self.state.value,
            "direction": self.direction,
            "entry_basis_bps": round(self.entry_basis_bps, 2),
            "exit_basis_bps": round(self.exit_basis_bps, 2),
            "hold_h": round(self.hold_h, 3),
            "entry": {l.venue: [round(l.qty_base, 8), round(l.filled_px, 8)] for l in self.entry_legs},
            "exit": {l.venue: [round(l.qty_base, 8), round(l.filled_px, 8)] for l in self.exit_legs},
            "notes": self.notes[-6:],
        }


@dataclass
class Intent:
    kind: str                      # quote | cancel | ioc | note
    order: Optional[OrderRequest] = None
    reason: str = ""
    venue: str = ""

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "reason": self.reason}
        if self.order:
            d.update({"venue": self.order.venue, "side": self.order.side,
                      "qty": round(self.order.qty_base, 8), "px": round(self.order.px, 8),
                      "tif": self.order.tif})
        return d


def passive_px(book: Book, side: str) -> float:
    """Price that *rests* on our own side of the book.

    A sell at the best bid is marketable (a resting bid is willing to buy at
    that price), so post-only would be rejected or auto-cancelled.  To be a
    maker you must join your own side: sell at the ask, buy at the bid.
    """
    return book.ask if side == "sell" else book.bid


def marketable_px(book: Book, side: str) -> float:
    """Price that crosses: sell into the bid, buy from the ask (used for IOC)."""
    return book.bid if side == "sell" else book.ask


def _direction(basis_bps: float) -> str:
    return "long_okx_short_lighter" if basis_bps >= 0 else "long_lighter_short_okx"


def _entry_orders(basis_bps: float, qty_okx: float, ratio: float, okx: Book, lt: Book) -> list[Leg]:
    """Both legs at the touch, on the correct sides."""
    if basis_bps >= 0:          # lighter rich -> sell lighter, buy okx
        return [
            Leg("lighter", "sell", qty_okx / ratio, px=passive_px(lt, "sell")),
            Leg("okx", "buy", qty_okx, px=passive_px(okx, "buy")),
        ]
    return [
        Leg("lighter", "buy", qty_okx / ratio, px=passive_px(lt, "buy")),
        Leg("okx", "sell", qty_okx, px=passive_px(okx, "sell")),
    ]


def _requote(order_leg: Leg, book: Book, params: WaveParams) -> Optional[OrderRequest]:
    """Re-peg a resting quote at the current touch (post-only, reduce_only off)."""
    if order_leg.remaining <= 0:
        return None
    px = passive_px(book, order_leg.side)
    if px <= 0:
        return None
    return OrderRequest(order_leg.venue, "", order_leg.side, order_leg.remaining, px,
                        order_type="limit", tif="post_only")


def _abort(round_: Round, filled: list[Leg], empty: list[Leg], _unused: float) -> list[Intent]:
    """Move a partially filled round into ABORT_UNWIND (passive unwind of the filled leg)."""
    round_.notes.append(f"abort: passive unwind of {len(filled)} leg(s)")
    round_.exit_legs = [Leg(l.venue, "sell" if l.side == "buy" else "buy", l.filled_qty, px=0.0)
                        for l in filled]
    round_.exit_state_ts = time.time()
    round_.state = RoundState.ABORT_UNWIND
    out = [Intent("cancel", reason="abort", venue=l.venue) for l in empty]
    return out


def decide(round_: Round, basis_bps: float, okx_book: Book, lt_book: Book,
           ratio: float, qty_okx: float, passive_net_bps: float) -> list[Intent]:
    """One poll of the state machine. Pure: no I/O, no clock beyond `time.time()`.

    Returns intents; the engine converts them into orders and calls back
    `on_fill` / `on_cancel` to advance the state.
    """
    p = round_.params
    out: list[Intent] = []

    if round_.state in (RoundState.FLAT,):
        if abs(basis_bps) < p.entry_bps:
            return [Intent("note", reason=f"|basis| {abs(basis_bps):.1f} < entry {p.entry_bps:.1f}")]
        if passive_net_bps < p.min_edge_bps:
            return [Intent("note", reason=f"passive net {passive_net_bps:.1f}bps below {p.min_edge_bps:.1f}")]
        round_.state = RoundState.ENTRY_QUOTING
        # reset every per-round artefact: reusing a Round without this leaked
        # `escalation_used` (so the 60-minute survival IOC never fired again),
        # kept stale `frozen` prices, and made every round share one round_id.
        round_.round_id += 1
        round_.escalation_used = False
        round_.frozen.clear()
        round_.exit_legs = []
        round_.exit_ts = 0.0
        round_.exit_state_ts = 0.0
        round_.entry_legs = []
        round_.direction = _direction(basis_bps)
        round_.entry_basis_bps = basis_bps
        round_.entry_ts = time.time()
        round_.entry_state_ts = round_.entry_ts
        round_.entry_legs = _entry_orders(basis_bps, qty_okx, ratio, okx_book, lt_book)
        round_.notes.append(f"entry quoted at |basis| {abs(basis_bps):.1f}bps")
        for leg in round_.entry_legs:
            book = lt_book if leg.venue == "lighter" else okx_book
            req = _requote(leg, book, p)
            if req:
                req.symbol = round_.symbol
                out.append(Intent("quote", req, "entry", leg.venue))
        return out

    if round_.state == RoundState.ENTRY_QUOTING:
        # dislocation gone before both filled -> cancel everything, no round
        if abs(basis_bps) <= p.exit_bps:
            round_.state = RoundState.FLAT
            round_.notes.append("entry cancelled: basis reverted before fill (no cost)")
            return [Intent("cancel", reason="basis reverted before fill", venue="all")]
        if round_.entry_age_s >= p.entry_deadline_s:
            round_.notes.append("entry timeout (nothing filled)")
            round_.state = RoundState.FLAT
            return [Intent("cancel", reason="entry timeout", venue="all")]
        for leg in round_.entry_legs:
            book = lt_book if leg.venue == "lighter" else okx_book
            req = _requote(leg, book, p)
            if req:
                req.symbol = round_.symbol
                out.append(Intent("quote", req, "entry re-peg", leg.venue))
        return out

    if round_.state == RoundState.ENTRY_PARTIAL:
        filled = [l for l in round_.entry_legs if l.filled_qty > 0]
        empty = [l for l in round_.entry_legs if l.filled_qty <= 0]
        # a naked leg is bounded by money and by adverse price, not only by time
        naked_usd = 0.0
        worst_adverse_bps = 0.0
        for leg in filled:
            book = lt_book if leg.venue == "lighter" else okx_book
            mark = book.mid or leg.filled_px
            naked_usd += abs(leg.filled_qty) * max(mark, 1e-12)
            sign = 1.0 if leg.side == "buy" else -1.0
            move_bps = (mark - leg.filled_px) / max(leg.filled_px, 1e-12) * 1e4 * sign
            worst_adverse_bps = min(worst_adverse_bps, move_bps)
        if naked_usd > p.max_unhedged_notional_usd:
            round_.notes.append(f"unhedged {naked_usd:.0f} USD > {p.max_unhedged_notional_usd:.0f} -> abort")
            return _abort(round_, filled, empty, -1.0)
        if worst_adverse_bps <= -p.adverse_move_stop_bps:
            round_.notes.append(f"naked leg {worst_adverse_bps:.0f}bps adverse -> abort")
            return _abort(round_, filled, empty, -1.0)
        # The dislocation we were paid to sell into is gone, or patience ran out:
        # give up on pairing and unwind the filled leg *passively*.
        if abs(basis_bps) <= p.exit_bps or round_.entry_age_s >= p.entry_deadline_s:
            why = ("basis reverted" if abs(basis_bps) <= p.exit_bps else "entry deadline")
            round_.notes.append(f"partial entry aborted ({why}): passive unwind of {len(filled)} leg(s)")
            round_.exit_legs = [Leg(l.venue, "sell" if l.side == "buy" else "buy",
                                    l.filled_qty, px=0.0) for l in filled]
            round_.exit_state_ts = time.time()
            round_.state = RoundState.ABORT_UNWIND
            for leg in empty:
                out.append(Intent("cancel", reason=f"abort ({why})", venue=leg.venue))
            return out + decide(round_, basis_bps, okx_book, lt_book, ratio, qty_okx, passive_net_bps)
        # patience: keep quoting the leg that has not filled
        for leg in empty:
            book = lt_book if leg.venue == "lighter" else okx_book
            req = _requote(leg, book, p)
            if req:
                req.symbol = round_.symbol
                out.append(Intent("quote", req, "waiting for the other leg", leg.venue))
        return out

    if round_.state == RoundState.ABORT_UNWIND:
        pending = [l for l in round_.exit_legs if l.remaining > 0]
        if not pending:
            round_.state = RoundState.FLAT
            round_.notes.append("abort finished: flat, slot free")
            return [Intent("note", reason="abort finished: flat")]
        age = time.time() - (round_.exit_state_ts or time.time())
        if age >= p.exit_deadline_s and not round_.escalation_used:
            round_.escalation_used = True
            round_.notes.append("abort unwind deadline hit -> single flagged IOC")
            return [Intent("ioc", OrderRequest(l.venue, round_.symbol, l.side, l.remaining,
                                               marketable_px(lt_book if l.venue == "lighter" else okx_book, l.side),
                                               order_type="limit", tif="ioc", reduce_only=True),
                           "abort escalation (cost)", l.venue) for l in pending]
        for leg in pending:
            book = lt_book if leg.venue == "lighter" else okx_book
            px = passive_px(book, leg.side)
            out.append(Intent("quote", OrderRequest(leg.venue, round_.symbol, leg.side,
                                                    leg.remaining, px, order_type="limit",
                                                    tif="post_only", reduce_only=True),
                              "abort unwind", leg.venue))
        return out

    if round_.state == RoundState.HEDGED:
        triggered = abs(basis_bps) <= p.exit_bps or round_.hold_h >= p.max_hold_h
        adverse = (basis_bps - round_.entry_basis_bps) if round_.direction == "long_okx_short_lighter" \
            else (round_.entry_basis_bps - basis_bps)
        if not triggered and adverse < p.adverse_basis_stop_bps:
            return [Intent("note", reason=f"holding: basis {basis_bps:+.1f}bps, {round_.hold_h:.2f}h")]
        round_.state = RoundState.EXIT_QUOTING
        round_.exit_ts = time.time()
        round_.exit_state_ts = round_.exit_ts
        round_.exit_basis_bps = basis_bps
        round_.exit_legs = []
        for leg in round_.entry_legs:
            book = lt_book if leg.venue == "lighter" else okx_book
            exit_side = "sell" if leg.side == "buy" else "buy"
            px = passive_px(book, exit_side)
            if p.freeze_px_on_trigger:
                round_.frozen[leg.venue] = px
            round_.exit_legs.append(Leg(leg.venue, exit_side, leg.filled_qty, px=px))
        round_.notes.append(f"exit triggered at basis {basis_bps:+.1f}bps "
                            f"({'time' if round_.hold_h >= p.max_hold_h else 'target'})")
        for leg in round_.exit_legs:
            px = round_.frozen.get(leg.venue, leg.px) if p.freeze_px_on_trigger else leg.px
            out.append(Intent("quote", OrderRequest(leg.venue, round_.symbol, leg.side,
                                                    leg.remaining, px, order_type="limit",
                                                    tif="post_only", reduce_only=True),
                              "exit (frozen price)" if p.freeze_px_on_trigger else "exit", leg.venue))
        return out

    if round_.state in (RoundState.EXIT_QUOTING, RoundState.EXIT_PARTIAL):
        pending = [l for l in round_.exit_legs if l.remaining > 0]
        if not pending:
            round_.state = RoundState.FLAT
            return [Intent("note", reason="round complete: both legs flat")]
        age = time.time() - (round_.exit_state_ts or round_.exit_ts or time.time())
        if age >= p.exit_deadline_s and not round_.escalation_used:
            round_.escalation_used = True
            round_.notes.append(f"exit deadline {p.exit_deadline_s:.0f}s hit -> single flagged IOC")
            intents = []
            for leg in pending:
                book = lt_book if leg.venue == "lighter" else okx_book
                px = marketable_px(book, leg.side)
                intents.append(Intent("ioc", OrderRequest(leg.venue, round_.symbol, leg.side,
                                                          leg.remaining, px, order_type="limit",
                                                          tif="ioc", reduce_only=True),
                                      "exit escalation (cost)", leg.venue))
            return intents
        for leg in pending:
            book = lt_book if leg.venue == "lighter" else okx_book
            if p.freeze_px_on_trigger:
                px = round_.frozen.get(leg.venue, leg.px)
            else:
                px = passive_px(book, leg.side)
            out.append(Intent("quote", OrderRequest(leg.venue, round_.symbol, leg.side,
                                                    leg.remaining, px, order_type="limit",
                                                    tif="post_only", reduce_only=True),
                              "exit re-peg", leg.venue))
        return out

    return [Intent("note", reason=f"unhandled state {round_.state}")]


# --------------------------------------------------------------------------- #
# fill bookkeeping (called by the engine when a venue reports a fill)
# --------------------------------------------------------------------------- #
def on_fill(round_: Round, venue: str, qty: float, px: float) -> None:
    legs = (round_.exit_legs
            if round_.state in (RoundState.EXIT_QUOTING, RoundState.EXIT_PARTIAL,
                                RoundState.ABORT_UNWIND)
            else round_.entry_legs)
    for leg in legs:
        if leg.venue != venue:
            continue
        leg.filled_qty = min(leg.qty_base, leg.filled_qty + qty)
        leg.filled_px = px
        break

    entry_filled = [l for l in round_.entry_legs if l.filled_qty > 0]
    if round_.state == RoundState.ENTRY_QUOTING and entry_filled:
        round_.state = RoundState.ENTRY_PARTIAL
        round_.entry_state_ts = round_.entry_state_ts or time.time()
    if round_.state in (RoundState.ENTRY_PARTIAL, RoundState.ENTRY_QUOTING) and \
            all(l.is_filled for l in round_.entry_legs):
        round_.state = RoundState.HEDGED
        round_.entry_ts = time.time()
    if round_.state in (RoundState.EXIT_QUOTING, RoundState.EXIT_PARTIAL):
        if not all(l.is_filled for l in round_.exit_legs):
            round_.state = RoundState.EXIT_PARTIAL
        else:
            round_.state = RoundState.FLAT
    if round_.state is RoundState.ABORT_UNWIND and round_.exit_legs \
            and all(l.is_filled for l in round_.exit_legs):
        round_.state = RoundState.FLAT
        round_.notes.append("abort unwind complete: flat")


def round_pnl_bps(round_: Round) -> Optional[float]:
    """Realised basis captured by the round, in bps of OKX notional."""
    lt = next((l for l in round_.entry_legs if l.venue == "lighter" and l.filled_qty > 0), None)
    ox = next((l for l in round_.entry_legs if l.venue == "okx" and l.filled_qty > 0), None)
    if not lt or not ox:
        return None
    entry_basis = ((lt.filled_px - ox.filled_px) / ox.filled_px) * 1e4 if ox.side == "buy" \
        else ((ox.filled_px - lt.filled_px) / ox.filled_px) * 1e4
    lt_x = next((l for l in round_.exit_legs if l.venue == "lighter" and l.filled_qty > 0), None)
    ox_x = next((l for l in round_.exit_legs if l.venue == "okx" and l.filled_qty > 0), None)
    if not lt_x or not ox_x:
        return None
    exit_basis = ((lt_x.filled_px - ox_x.filled_px) / ox_x.filled_px) * 1e4 if ox_x.side == "buy" \
        else ((ox_x.filled_px - lt_x.filled_px) / ox_x.filled_px) * 1e4
    return entry_basis - exit_basis