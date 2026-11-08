"""P2-8  Passive exit queue: intents instead of market orders.

The failure mode this replaces: a depth-aware closer that walks slippage
20 -> 40 -> 80 -> 200 bps because it insists on leaving *now*.  On an illiquid
leg that 200 bps is not slippage, it is the price of impatience, and it is
exactly the liquidity a patient counterparty is waiting to sell you.

So exits become a queue of tickets:

* the ticket has a deadline and a ladder (post-only at 0 / 2 / 5 bps from touch);
* every poll it is re-priced to stay the best passive quote on its side;
* it only escalates to IOC when the deadline is close, and only to a *market*
  order when the caller marks the ticket `forced` (risk stop).

Everything is deterministic and testable: `plan()` takes a book and a clock and
returns at most one order per poll.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .models import Book, OrderRequest


@dataclass
class ExitTicket:
    symbol: str
    venue: str
    side: str                     # sell to exit a long, buy to exit a short
    qty_base: float
    remaining: float
    created_at: float = field(default_factory=time.time)
    deadline_s: float = 900.0     # 15 min of patience by default
    ladder_bps: tuple[float, ...] = (0.0, 2.0, 5.0)
    max_passive_bps: float = 10.0
    escalation_bps: float = 25.0  # IOC cap near the deadline
    forced: bool = False          # risk stop: market order allowed
    fills: list[float] = field(default_factory=list)

    @property
    def age_s(self) -> float:
        return time.time() - self.created_at

    @property
    def time_left(self) -> float:
        return self.deadline_s - self.age_s

    @property
    def done(self) -> bool:
        return self.remaining <= 1e-9

    def fill(self, qty: float) -> None:
        self.fills.append(qty)
        self.remaining = max(0.0, self.remaining - qty)


class ExitQueue:
    def __init__(self) -> None:
        self.tickets: list[ExitTicket] = []

    def add(self, ticket: ExitTicket) -> ExitTicket:
        existing = self.find(ticket.symbol, ticket.venue, ticket.side)
        if existing and not existing.done:
            existing.remaining += ticket.remaining
            return existing
        self.tickets.append(ticket)
        return ticket

    def find(self, symbol: str, venue: str, side: str) -> Optional[ExitTicket]:
        for t in self.tickets:
            if t.symbol == symbol and t.venue == venue and t.side == side and not t.done:
                return t
        return None

    def open_tickets(self) -> list[ExitTicket]:
        self.tickets = [t for t in self.tickets if not t.done]
        return list(self.tickets)

    def plan(self, ticket: ExitTicket, book: Book, qty_step: float = 0.0) -> Optional[OrderRequest]:
        """One order per poll for one ticket."""
        if ticket.done or not book.bids or not book.asks:
            return None
        qty = ticket.remaining if qty_step <= 0 else min(ticket.remaining, qty_step)

        passive_side = book.bid if ticket.side == "sell" else book.ask
        sign = -1.0 if ticket.side == "sell" else 1.0

        if ticket.forced:
            return OrderRequest(ticket.venue, ticket.symbol, ticket.side, qty,
                                passive_side, order_type="market", tif="ioc", reduce_only=True)

        if ticket.time_left <= 60:
            # escalate: pay up to escalation_bps, still a limit order
            px = passive_side * (1 + sign * ticket.escalation_bps / 1e4)
            return OrderRequest(ticket.venue, ticket.symbol, ticket.side, qty, px,
                                order_type="limit", tif="ioc", reduce_only=True)

        # patience: stay the best passive quote, honouring the ladder
        px = _best_ladder_px(book, ticket.side, ticket.ladder_bps, ticket.max_passive_bps)
        if px <= 0:
            px = passive_side
        return OrderRequest(ticket.venue, ticket.symbol, ticket.side, qty, px,
                            order_type="limit", tif="post_only", reduce_only=True)


def _best_ladder_px(book: Book, side: str, ladder_bps: tuple[float, ...], max_passive_bps: float) -> float:
    """Pick the most aggressive *passive* price that is still inside the ladder."""
    touch = book.bid if side == "sell" else book.ask
    opposite = book.ask if side == "sell" else book.bid
    if touch <= 0 or opposite <= 0:
        return 0.0
    for bps in sorted(ladder_bps):
        if bps > max_passive_bps:
            continue
        px = touch * (1 - bps / 1e4) if side == "sell" else touch * (1 + bps / 1e4)
        # never cross the touch: a post-only order that would cross is rejected
        if side == "sell" and px <= opposite:
            return px
        if side == "buy" and px >= opposite:
            return px
    return touch