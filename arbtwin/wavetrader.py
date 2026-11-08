"""Execution adapter for `arbtwin.wave`: turns intents into resting quotes.

The state machine in `wave.py` is pure.  This layer owns the messy part:

* which post-only quotes are currently resting per (symbol, venue);
* when a resting quote should be considered filled (the book crossed it);
* pushing fills back into the round (`wave.on_fill`), and
* writing every round transition to `state/rounds.jsonl` so the round history is
  auditable after the fact.

Dry-run and live share the same fill rule: a post-only buy at `p` is filled when
the venue's best ask trades down to `p` (someone crossed into us), and a sell at
`p` is filled when the best bid trades up to `p`.  That is conservative — a real
fill can only happen at *our* price — and it makes dry-run behave like live
without inventing queue position.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import wave
from .models import Book, Fill, OrderRequest


@dataclass
class RestingQuote:
    symbol: str
    venue: str
    side: str
    px: float
    qty_base: float
    placed_ts: float = field(default_factory=time.time)
    order_id: str = ""
    reduce_only: bool = False

    def crossed(self, book: Book) -> Optional[float]:
        """Has the book traded *through* our resting price?

        Quoting at the touch means our price equals the best bid/ask, so a naive
        `book.bid >= px` would report an instant self-fill on every quote.  The
        honest dry-run proxy is a move-through: a resting sell fills only once
        the best bid is strictly above our price (the level was consumed and the
        market moved up), a resting buy only once the best ask is strictly below.
        That understates fills rather than inventing them — the live path uses the
        venue's own fill reports, not this heuristic.
        """
        if not book.bids or not book.asks or self.qty_base <= 0:
            return None
        eps = max(abs(self.px) * 1e-6, 1e-12)
        if self.side == "buy" and book.ask < self.px - eps:
            return self.px
        if self.side == "sell" and book.bid > self.px + eps:
            return self.px
        return None


class WaveTrader:
    """One round per symbol, limit-only, both legs to flat before the next round."""

    def __init__(self, params_by_symbol: dict[str, wave.WaveParams],
                 place: Callable[[OrderRequest], Fill],
                 cancel: Callable[[str, str, str], bool] | None = None,
                 persist: Callable[[dict], None] | None = None,
                 simulate_fills: bool = True,
                 on_error: Callable[[str], None] | None = None):
        self.params = params_by_symbol
        self.place = place
        self.cancel = cancel or (lambda symbol, venue, order_id: True)
        self.persist = persist or (lambda payload: None)
        # "did the book trade through my price" is a *dry-run* proxy.  In live mode
        # fills must come from the venue (order state / fills channel); inventing
        # them from the book would silently desynchronise the round from the account.
        self.simulate_fills = simulate_fills
        self.on_error = on_error or (lambda msg: None)
        self.rounds: dict[str, wave.Round] = {}
        self.quotes: dict[tuple[str, str], RestingQuote] = {}
        self._seq = 0

    # ------------------------------------------------------------------ helpers
    def round_for(self, symbol: str) -> wave.Round:
        if symbol not in self.rounds:
            self._seq += 1
            self.rounds[symbol] = wave.Round(symbol=symbol,
                                             params=self.params[symbol], round_id=self._seq)
        return self.rounds[symbol]

    def _drop_quotes(self, symbol: str, venue: str | None = None) -> None:
        for key in [k for k in self.quotes if k[0] == symbol and (venue in (None, k[1]))]:
            q = self.quotes.pop(key)
            if not self.cancel(symbol, q.venue, q.order_id):
                # A failed cancel is a *live* order that can still fill; it must be
                # visible, not swallowed (it would desync the round on the next fill).
                self.on_error(f"cancel failed: {q.venue} {symbol} order={q.order_id} px={q.px}")

    def open_quotes(self, symbol: str) -> list[RestingQuote]:
        return [q for (s, _), q in self.quotes.items() if s == symbol]

    # ------------------------------------------------------------------ polling
    def poll_fills(self, symbol: str, okx_book: Book, lt_book: Book) -> list[dict]:
        """Advance the round for every resting quote the book has traded through."""
        rnd = self.round_for(symbol)
        events: list[dict] = []
        if not self.simulate_fills:
            return events
        for key, quote in list(self.quotes.items()):
            if key[0] != symbol:
                continue
            book = lt_book if quote.venue == "lighter" else okx_book
            px = quote.crossed(book)
            if px is None:
                continue
            self.quotes.pop(key, None)
            wave.on_fill(rnd, quote.venue, quote.qty_base, px)
            events.append({"kind": "fill", "venue": quote.venue, "side": quote.side,
                           "qty": quote.qty_base, "px": px, "state": rnd.state.value})
        if events:
            self._persist(rnd, events)
        return events

    # ------------------------------------------------------------------ stepping
    def step(self, symbol: str, basis_bps: float, okx_book: Book, lt_book: Book,
             ratio: float, qty_okx: float, passive_net_bps: float) -> dict:
        rnd = self.round_for(symbol)
        before = rnd.state
        intents = wave.decide(rnd, basis_bps, okx_book, lt_book, ratio, qty_okx, passive_net_bps)
        executed: list[dict] = []

        for intent in intents:
            if intent.kind == "quote" and intent.order is not None:
                order = intent.order
                order.symbol = symbol
                if rnd.state is wave.RoundState.FLAT:
                    continue
                existing = self.quotes.get((symbol, order.venue))
                if (existing and existing.side == order.side
                        and abs(existing.px - order.px) <= max(1e-12, abs(order.px) * 1e-9)
                        and abs(existing.qty_base - order.qty_base) <= 1e-12):
                    # same price, same size: cancelling and re-placing every poll
                    # would churn the nonce stream (and one venue charges latency
                    # for it) with no effect on queue position.
                    executed.append({"kind": "hold", "venue": order.venue, "px": order.px})
                    continue
                self._drop_quotes(symbol, order.venue)          # re-peg: one quote per venue
                try:
                    fill = self.place(order)
                    self.quotes[(symbol, order.venue)] = RestingQuote(
                        symbol, order.venue, order.side, order.px, order.qty_base,
                        order_id=getattr(fill, "order_id", ""), reduce_only=order.reduce_only)
                    executed.append({"kind": "quote", "venue": order.venue, "side": order.side,
                                     "px": order.px, "qty": order.qty_base})
                except Exception as exc:                        # venue said no
                    executed.append({"kind": "reject", "venue": order.venue, "error": str(exc)[:120]})
            elif intent.kind == "cancel":
                self._drop_quotes(symbol, intent.venue or None)
                executed.append({"kind": "cancel", "venue": intent.venue or "all"})
            elif intent.kind == "ioc" and intent.order is not None:
                order = intent.order
                order.symbol = symbol
                self._drop_quotes(symbol, order.venue)
                try:
                    fill = self.place(order)
                    wave.on_fill(rnd, order.venue, order.qty_base, fill.px)
                    executed.append({"kind": "ioc", "venue": order.venue, "px": fill.px,
                                     "qty": order.qty_base})
                except Exception as exc:
                    executed.append({"kind": "reject", "venue": order.venue, "error": str(exc)[:120]})

        payload = {"round": rnd.to_dict(), "intents": [i.to_dict() for i in intents],
                   "executed": executed, "state_before": before.value}
        if before is not rnd.state or executed:
            self._persist(rnd, executed)
        return payload

    # ------------------------------------------------------------------ bookkeeping
    def _persist(self, rnd: wave.Round, events: list[dict]) -> None:
        record = rnd.to_dict()
        record["events"] = events
        record["pnl_bps"] = wave.round_pnl_bps(rnd)
        self.persist(record)

    def completed_rounds(self) -> list[dict]:
        out = []
        for rnd in self.rounds.values():
            if rnd.state is wave.RoundState.FLAT and rnd.exit_legs:
                out.append({**rnd.to_dict(), "pnl_bps": wave.round_pnl_bps(rnd)})
        return out

    def reset(self, symbol: str) -> None:
        self._drop_quotes(symbol)
        self.rounds.pop(symbol, None)