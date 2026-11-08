"""Wave round state machine: one round, limit-only, both legs to flat.

These tests encode the operator's rules literally, because they are the rules that
are hard to keep straight in code:

1. a round owns the symbol until **both** legs are flat (no overlapping rounds);
2. if one leg fills first, the other keeps being quoted — we do not abandon it
   immediately, and we do not market-order the hedge;
3. the exit is a passive quote at the trigger-time price, and the other leg waits;
4. only a deadline (or a risk stop) may pay taker fees, and it does so once.
"""

from __future__ import annotations

import time

import pytest

from arbtwin.models import Book
from arbtwin.wave import (Leg, Round, RoundState, WaveParams, decide, on_fill,
                          round_pnl_bps)


def mk_books(okx_mid=100.0, lt_offset_bps=0.0, size=500.0) -> tuple[Book, Book]:
    okx = Book("okx", "X", bids=[(okx_mid - 0.02, size)], asks=[(okx_mid + 0.02, size)])
    rich = okx_mid * (1 + lt_offset_bps / 1e4)
    lt = Book("lighter", "X", bids=[(rich - 0.02, size)], asks=[(rich + 0.02, size)])
    return okx, lt


def mk_round(entry_bps=14.0, exit_bps=4.5) -> Round:
    return Round(symbol="X", params=WaveParams(entry_bps=entry_bps, exit_bps=exit_bps),
                 round_id=1)


def test_flat_waits_below_entry_threshold():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=6.0)
    intents = decide(r, 6.0, okx, lt, 1.0, qty_okx=4.0, passive_net_bps=5.0)
    assert r.state is RoundState.FLAT
    assert intents and intents[0].kind == "note"


def test_entry_quotes_both_legs_on_the_correct_sides():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)      # lighter rich
    intents = decide(r, 20.0, okx, lt, 1.0, qty_okx=4.0, passive_net_bps=16.0)
    assert r.state is RoundState.ENTRY_QUOTING
    quotes = [i for i in intents if i.kind == "quote"]
    assert {q.venue for q in quotes} == {"okx", "lighter"}
    by_venue = {q.venue: q for q in quotes}
    assert by_venue["lighter"].order.side == "sell"
    assert by_venue["okx"].order.side == "buy"
    assert all(q.order.tif == "post_only" for q in quotes)


def test_one_leg_filled_keeps_quoting_the_other_and_never_markets():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", qty=4.0, px=lt.bid)          # sell leg fills first
    assert r.state is RoundState.ENTRY_PARTIAL
    intents = decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    assert [i.kind for i in intents] == ["quote"]
    assert intents[0].venue == "okx" and intents[0].order.tif == "post_only"
    assert intents[0].order.side == "buy"


def test_both_filled_means_hedged_then_exit_at_p50():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    on_fill(r, "okx", 4.0, okx.ask)
    assert r.state is RoundState.HEDGED

    # still dislocated -> hold
    hold = decide(r, 18.0, okx, lt, 1.0, 4.0, 14.0)
    assert r.state is RoundState.HEDGED and hold[0].kind == "note"

    # converged to p50 -> exit quotes at frozen trigger prices, reduce_only
    ex = decide(r, 4.0, okx, lt, 1.0, 4.0, 0.0)
    assert r.state is RoundState.EXIT_QUOTING
    quotes = [i for i in ex if i.kind == "quote"]
    assert {q.venue for q in quotes} == {"okx", "lighter"}
    assert all(q.order.reduce_only for q in quotes)
    assert quotes[0].order.px == r.frozen[quotes[0].venue]


def test_exit_waits_for_the_second_leg_at_the_frozen_price():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    on_fill(r, "okx", 4.0, okx.ask)
    frozen_px = {l.venue: l.px for l in r.exit_legs}
    decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)      # triggers exit, freezes prices
    on_fill(r, "lighter", 4.0, r.frozen["lighter"])
    assert r.state is RoundState.EXIT_PARTIAL
    intents = decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)
    assert len(intents) == 1 and intents[0].venue == "okx"
    # the remaining leg keeps the frozen price, it does not chase
    assert intents[0].order.px == r.frozen["okx"]
    on_fill(r, "okx", 4.0, r.frozen["okx"])
    assert r.state is RoundState.FLAT              # slot becomes free


def test_round_is_not_reentered_while_closing():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    on_fill(r, "okx", 4.0, okx.ask)
    decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)          # closing
    # basis dislocates hugely again: no new entry may start
    intents = decide(r, 40.0, okx, lt, 1.0, 4.0, 30.0)
    assert r.state in (RoundState.EXIT_QUOTING, RoundState.EXIT_PARTIAL)
    assert all(i.kind != "quote" or i.order.reduce_only for i in intents)


def test_partial_entry_aborts_passively_when_basis_reverts():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    intents = decide(r, 3.0, okx, lt, 1.0, 4.0, -1.0)   # basis collapsed while naked
    assert r.state is RoundState.ABORT_UNWIND
    kinds = [i.kind for i in intents]
    assert "cancel" in kinds                    # the unfilled leg is pulled
    unwinds = [i for i in intents if i.kind == "quote"]
    assert unwinds and all(i.order.reduce_only and i.order.tif == "post_only" for i in unwinds)


def test_entry_timeout_does_not_market_the_naked_leg():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    r.entry_state_ts -= 10_000                  # older than the deadline
    intents = decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    assert r.state is RoundState.ABORT_UNWIND
    assert all(i.kind != "ioc" for i in intents)


def test_exit_escalates_once_after_the_deadline():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)
    on_fill(r, "okx", 4.0, okx.ask)
    decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)
    r.exit_state_ts -= 10_000                   # past exit_deadline_s
    first = decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)
    assert any(i.kind == "ioc" for i in first)
    assert all(i.order.reduce_only for i in first if i.kind == "ioc")
    second = decide(r, 3.0, okx, lt, 1.0, 4.0, 0.0)   # only once
    assert not any(i.kind == "ioc" for i in second)


def test_pnl_of_a_completed_round():
    r = mk_round()
    okx, lt = mk_books(lt_offset_bps=20.0)
    decide(r, 20.0, okx, lt, 1.0, 4.0, 16.0)
    on_fill(r, "lighter", 4.0, lt.bid)          # sell rich
    on_fill(r, "okx", 4.0, okx.ask)             # buy cheap
    decide(r, 4.0, okx, lt, 1.0, 4.0, 0.0)
    on_fill(r, "lighter", 4.0, r.frozen["lighter"])
    on_fill(r, "okx", 4.0, r.frozen["okx"])
    assert r.state is RoundState.FLAT
    pnl = round_pnl_bps(r)
    assert pnl is not None and pnl > 0

def test_unchanged_quote_is_not_cancelled_and_replaced():
    from arbtwin.wavetrader import WaveTrader
    from arbtwin.models import Book, Fill

    placed, cancelled = [], []
    trader = WaveTrader(
        {"X": WaveParams(entry_bps=14.0, exit_bps=4.5)},
        place=lambda o: (placed.append((o.venue, o.side, o.px)),
                         Fill(o.venue, "X", o.side, o.qty_base, o.px))[1],
        cancel=lambda s, v, oid: cancelled.append((v, oid)) or True,
    )
    okx = Book("okx", "X", bids=[(99.98, 500)], asks=[(100.02, 500)])
    lt = Book("lighter", "X", bids=[(100.22, 500)], asks=[(100.24, 500)])
    trader.step("X", 20.0, okx, lt, 1.0, 4.0, 16.0)      # first poll: place both legs
    assert len(placed) == 2
    trader.step("X", 20.0, okx, lt, 1.0, 4.0, 16.0)      # unchanged prices: no churn
    assert len(placed) == 2 and cancelled == []
    okx2 = Book("okx", "X", bids=[(99.96, 500)], asks=[(100.00, 500)])  # touch moved
    trader.step("X", 20.0, okx2, lt, 1.0, 4.0, 16.0)
    assert len(placed) == 3 and cancelled              # only the moved leg is re-pegged
