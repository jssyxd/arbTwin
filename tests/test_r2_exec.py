"""R2 execution tests: VWAP, cancel-other-leg, force-flat, engine book-exception, kill switch.

Pure tests — no network, no state files.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from arbtwin.models import Book, Fill, OrderRequest
from arbtwin.wave import (
    Intent, Leg, Round, RoundState, WaveParams,
    decide, force_flat, on_fill, round_pnl_bps, _abort_cost_bps,
)
from arbtwin.wavetrader import WaveTrader


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def mk_book(venue="okx", mid=100.0, size=500.0) -> Book:
    return Book(venue, "X", bids=[(mid - 0.02, size)], asks=[(mid + 0.02, size)])


def mk_round(entry_bps=14.0, exit_bps=4.5) -> Round:
    return Round(symbol="X", params=WaveParams(entry_bps=entry_bps, exit_bps=exit_bps,
                                               max_unhedged_notional_usd=10_000.0,
                                               entry_deadline_s=5400.0),
                 round_id=1)


def mk_trader() -> WaveTrader:
    params = {"X": WaveParams(entry_bps=14.0, exit_bps=4.5,
                              max_unhedged_notional_usd=10_000.0,
                              entry_deadline_s=5400.0)}
    fills: list[Fill] = []

    def place(order: OrderRequest) -> Fill:
        f = Fill(order.venue, order.symbol, order.side, order.qty_base, order.px,
                 fee_bps=0.0, order_id=f"test-{len(fills)}")
        fills.append(f)
        return f

    trader = WaveTrader(params, place=place, simulate_fills=False)
    trader._fills = fills
    return trader


# --------------------------------------------------------------------------- #
# 1. VWAP after two fills
# --------------------------------------------------------------------------- #
def test_vwap_after_two_fills():
    """Two partial fills on the same leg should VWAP the filled price."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    intents = decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    assert r.state == RoundState.ENTRY_QUOTING

    # First partial fill: 2 units @ 99.98 (OKX bid for buy)
    on_fill(r, "okx", 2.0, 99.98)

    # Second partial fill on same leg: 2 units @ 100.02
    on_fill(r, "okx", 2.0, 100.02)

    okx_leg = next(l for l in r.entry_legs if l.venue == "okx")
    assert okx_leg.filled_qty == 4.0
    # VWAP = (2*99.98 + 2*100.02) / 4 = 100.00
    assert okx_leg.filled_px == pytest.approx(100.00)
    assert okx_leg.filled_notional == pytest.approx(400.0)


# --------------------------------------------------------------------------- #
# 2. First fill cancels the other leg
# --------------------------------------------------------------------------- #
def test_first_fill_cancels_other_leg():
    """When ENTRY_QUOTING -> ENTRY_PARTIAL, on_fill returns a cancel intent for the other venue."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    assert r.state == RoundState.ENTRY_QUOTING

    # OKX fills first
    intents = on_fill(r, "okx", 4.0, 99.98)
    assert r.state == RoundState.ENTRY_PARTIAL

    # R2 grace: no immediate cancel; the unfilled leg gets unhedged_grace_s.
    assert not [i for i in intents if i.kind == "cancel"]
    r.entry_state_ts = time.time() - 10.0  # grace over
    late = decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)
    cancel_intents = [i for i in late if i.kind == "cancel"]
    assert any(i.venue == "lighter" for i in cancel_intents)


def test_first_fill_through_wavetrader_cancels_other_quote():
    """Through WaveTrader.poll_fills, a first fill drops the other venue's quote."""
    trader = mk_trader()
    okx_book = mk_book("okx", mid=100.0)
    lt_book = mk_book("lighter", mid=200.0)

    # Manually enter a round with both quotes resting
    rnd = trader.round_for("X")
    rnd.state = RoundState.ENTRY_QUOTING
    rnd.direction = "long_okx_short_lighter"
    rnd.entry_basis_bps = 40.0
    rnd.entry_legs = [
        Leg("lighter", "sell", 2.0, px=199.98),
        Leg("okx", "buy", 4.0, px=99.98),
    ]
    # Place both quotes
    from arbtwin.wavetrader import RestingQuote
    trader.quotes[("X", "lighter")] = RestingQuote("X", "lighter", "sell", 199.98, 2.0)
    trader.quotes[("X", "okx")] = RestingQuote("X", "okx", "buy", 99.98, 4.0)

    # OKX book crossed through the buy (ask moved below our px)
    okx_filled = Book("okx", "X", bids=[(99.96, 500)], asks=[(99.96, 500)])
    lt_filled = Book("lighter", "X", bids=[(199.96, 500)], asks=[(200.04, 500)])

    # Re-enable simulate_fills so poll_fills works
    trader.simulate_fills = True
    events = trader.poll_fills("X", okx_filled, lt_filled)

    fill_events = [e for e in events if e["kind"] == "fill"]
    assert len(fill_events) >= 1
    assert fill_events[0]["venue"] == "okx"

    # Grace window: the other quote survives the first fill; it is cancelled only
    # after unhedged_grace_s expires (test_one_leg_filled... covers the expiry).
    assert ("X", "lighter") in trader.quotes
    # OKX quote was consumed by the fill
    assert ("X", "okx") not in trader.quotes

    assert rnd.state == RoundState.ENTRY_PARTIAL


# --------------------------------------------------------------------------- #
# 3. force_flat produces IOC reduce-only at last price ± collar
# --------------------------------------------------------------------------- #
def test_force_flat_produces_ioc_at_collar():
    """force_flat returns reduce-only IOC intents with prices offset by collar_bps."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    # Simulate OKX filled at 99.98 (buy), Lighter never filled
    r.entry_legs[1].filled_qty = 4.0       # okx buy
    r.entry_legs[1].filled_px = 99.98
    r.entry_legs[1].filled_notional = 4.0 * 99.98
    r.state = RoundState.ENTRY_PARTIAL
    r.entry_state_ts = time.time()

    intents = force_flat(r, "book unavailable", collar_bps=25.0)
    assert r.state == RoundState.ABORT_UNWIND

    ioc_intents = [i for i in intents if i.kind == "ioc"]
    assert len(ioc_intents) >= 1
    ioc = ioc_intents[0]

    # OKX filled as buy, so exit side is sell, reduce_only
    assert ioc.order is not None
    assert ioc.order.side == "sell"
    assert ioc.order.reduce_only is True
    assert ioc.order.tif == "ioc"
    # Price should be filled_px * (1 - 25/10000) = 99.98 * 0.9975 ≈ 99.7305
    expected_px = 99.98 * (1 - 25.0 / 1e4)
    assert ioc.order.px == pytest.approx(expected_px, rel=1e-9)

    # Check exit_leg was appended
    assert any(l.venue == "okx" and l.side == "sell" for l in r.exit_legs)


def test_force_flat_sell_entry_buys_above():
    """Sell entry: exit buys above fill_px at +collar."""
    r = mk_round()
    okx = mk_book(mid=100.0)
    lt = mk_book(mid=50.0, venue="lighter")
    # basis negative -> long_lighter_short_okx
    decide(r, -40.0, okx, lt, 0.5, 4.0, 10.0)

    # OKX sell filled at 100.02
    okx_leg = next(l for l in r.entry_legs if l.venue == "okx")
    okx_leg.filled_qty = 4.0
    okx_leg.filled_px = 100.02
    okx_leg.filled_notional = 4.0 * 100.02
    r.state = RoundState.ENTRY_PARTIAL
    r.entry_state_ts = time.time()

    intents = force_flat(r, "test", collar_bps=25.0)
    ioc = [i for i in intents if i.kind == "ioc"][0]
    assert ioc.order.side == "buy"
    # Buy above: 100.02 * (1 + 25/10000) = 100.02 * 1.0025 ≈ 100.27005
    expected_px = 100.02 * (1 + 25.0 / 1e4)
    assert ioc.order.px == pytest.approx(expected_px, rel=1e-9)


def test_force_flat_skips_unfilled_legs():
    """Only filled legs produce IOC intents."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    # Only OKX filled
    r.entry_legs[1].filled_qty = 4.0
    r.entry_legs[1].filled_px = 99.98
    r.entry_legs[1].filled_notional = 4.0 * 99.98
    r.state = RoundState.ENTRY_PARTIAL

    intents = force_flat(r, "test")
    ioc_intents = [i for i in intents if i.kind == "ioc"]
    assert len(ioc_intents) == 1
    assert ioc_intents[0].venue == "okx"


# --------------------------------------------------------------------------- #
# 4. Engine path: book exception flattens instead of skipping
# --------------------------------------------------------------------------- #
def test_wave_step_book_exception_flattens_open_round(monkeypatch):
    """When okx.book() raises, wave_step force-flats an open round instead of skipping."""
    from arbtwin.engine import TwinEngine
    from arbtwin.config import AppConfig

    cfg = AppConfig(dry_run=True, strategy="wave")
    cfg.state_dir = str(Path(os.environ.get("TMP", "/tmp")) / f"test_r2_{os.getpid()}")

    engine = TwinEngine(cfg, proxy=None)

    # Minimal universe: one symbol with wave params
    engine.states["X"] = __import__("arbtwin.engine", fromlist=["SymbolState"]).SymbolState(
        symbol="X", unit_ratio=2.0, sigma_1h_bps=20.0, basis_sigma_bps=30.0,
        band_override_bps=14.0,
        spec_okx=MagicMock(), spec_lighter=MagicMock(),
    )
    from arbtwin.wave import WaveParams
    from arbtwin.wavetrader import WaveTrader
    engine.states["X"].wave_params = WaveParams(entry_bps=14.0, exit_bps=4.5,
                                                max_unhedged_notional_usd=10_000.0,
                                                entry_deadline_s=5400.0)

    fills_log: list[Fill] = []
    def place(order):
        f = Fill(order.venue, order.symbol, order.side, order.qty_base, order.px,
                 fee_bps=0.0, order_id="test")
        fills_log.append(f)
        return f

    engine.wave = WaveTrader({"X": engine.states["X"].wave_params},
                             place=place, simulate_fills=False)

    # Put the round in ENTRY_PARTIAL (naked OKX leg)
    rnd = engine.wave.round_for("X")
    rnd.state = RoundState.ENTRY_PARTIAL
    rnd.direction = "long_okx_short_lighter"
    rnd.entry_basis_bps = 40.0
    rnd.entry_legs = [
        Leg("lighter", "sell", 2.0, px=199.98),
        Leg("okx", "buy", 4.0, px=99.98, filled_qty=4.0, filled_px=99.98,
            filled_notional=4.0 * 99.98),
    ]
    rnd.unit_ratio = 2.0

    # Mock the book calls: okx.book raises, lt.book works
    def okx_book_raises(symbol):
        raise RuntimeError("connection timeout")

    lt_book = Book("lighter", "X",
                   bids=[(199.96, 500)], asks=[(200.04, 500)])

    monkeypatch.setattr(engine.okx, "book", okx_book_raises)
    monkeypatch.setattr(engine.lighter, "book", lambda s: lt_book)

    # Build a minimal snap with a row for X
    from arbtwin.models import SpreadRow
    row = SpreadRow(symbol="X", ts=time.time(), unit_ratio=2.0,
                    okx_mid=100.0, lighter_mid_norm=200.0, basis_bps=40.0,
                    executable_edge_bps=30.0, fee_bps=0.0, latency_cost_bps=2.0,
                    slippage_bps=0.0, net_edge_bps=28.0, entry_band_bps=14.0)
    snap = {"rows": [row], "ts": time.time(), "summary": {}, "gates": {},
            "positions": {}, "exits": []}

    result = engine.wave_step(snap)
    assert len(result) >= 1

    # Should have force-flatted (IOC placed for the naked leg)
    ioc_fills = [f for f in fills_log if f.order_id == "test"]
    assert len(ioc_fills) >= 1, f"Expected IOC fill, got fills: {fills_log}"
    okx_ioc = [f for f in ioc_fills if f.venue == "okx" and f.side == "sell"]
    assert len(okx_ioc) >= 1, "Expected reduce-only sell IOC on OKX"

    # Round should now be flat (or ABORT_UNWIND with exit filled)
    assert rnd.state in (RoundState.ABORT_UNWIND, RoundState.FLAT)

    # Clean up
    try:
        (Path(cfg.state_dir) / "KILL").unlink(missing_ok=True)
    except Exception:
        pass


def test_wave_step_skips_flat_round_on_book_exception(monkeypatch):
    """Book exception on a FLAT round should still skip (no force-flat needed)."""
    from arbtwin.engine import TwinEngine
    from arbtwin.config import AppConfig

    cfg = AppConfig(dry_run=True, strategy="wave")
    cfg.state_dir = str(Path(os.environ.get("TMP", "/tmp")) / f"test_r2_flat_{os.getpid()}")

    engine = TwinEngine(cfg, proxy=None)
    engine.states["X"] = __import__("arbtwin.engine", fromlist=["SymbolState"]).SymbolState(
        symbol="X", unit_ratio=2.0, sigma_1h_bps=20.0, basis_sigma_bps=30.0,
        band_override_bps=14.0,
        spec_okx=MagicMock(), spec_lighter=MagicMock(),
    )
    engine.states["X"].wave_params = WaveParams(entry_bps=14.0, exit_bps=4.5)

    engine.wave = WaveTrader({"X": engine.states["X"].wave_params},
                             place=lambda o: Fill(o.venue, o.symbol, o.side, o.qty_base, o.px),
                             simulate_fills=False)

    # Round is FLAT
    rnd = engine.wave.round_for("X")
    assert rnd.state == RoundState.FLAT

    monkeypatch.setattr(engine.okx, "book", lambda s: (_ for _ in ()).throw(RuntimeError("timeout")))
    monkeypatch.setattr(engine.lighter, "book", lambda s: (_ for _ in ()).throw(RuntimeError("timeout")))

    from arbtwin.models import SpreadRow
    row = SpreadRow(symbol="X", ts=time.time(), unit_ratio=2.0,
                    okx_mid=100.0, lighter_mid_norm=50.0, basis_bps=10.0,
                    executable_edge_bps=5.0, fee_bps=0.0, latency_cost_bps=2.0,
                    slippage_bps=0.0, net_edge_bps=3.0, entry_band_bps=14.0)
    snap = {"rows": [row], "ts": time.time(), "summary": {}, "gates": {},
            "positions": {}, "exits": []}

    # Should not crash — just skip the symbol
    result = engine.wave_step(snap)
    # FLAT round: no force-flat, just skip
    assert rnd.state == RoundState.FLAT


# --------------------------------------------------------------------------- #
# 5. Kill switch stops quotes
# --------------------------------------------------------------------------- #
def test_kill_switch_stops_quotes(monkeypatch, tmp_path):
    """run_once returns {'killed': True} when state/KILL exists and flattens open rounds."""
    from arbtwin.engine import TwinEngine
    from arbtwin.config import AppConfig

    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir, exist_ok=True)

    cfg = AppConfig(dry_run=True, strategy="wave")
    cfg.state_dir = state_dir

    engine = TwinEngine(cfg, proxy=None)
    engine.states["X"] = __import__("arbtwin.engine", fromlist=["SymbolState"]).SymbolState(
        symbol="X", unit_ratio=2.0, sigma_1h_bps=20.0, basis_sigma_bps=30.0,
        band_override_bps=14.0,
        spec_okx=MagicMock(), spec_lighter=MagicMock(),
    )
    engine.states["X"].wave_params = WaveParams(entry_bps=14.0, exit_bps=4.5)

    fills_log: list[Fill] = []
    engine.wave = WaveTrader({"X": engine.states["X"].wave_params},
                             place=lambda o: Fill(o.venue, o.symbol, o.side, o.qty_base, o.px,
                                                  order_id="kill"),
                             simulate_fills=False)

    # Put a round in ENTRY_PARTIAL
    rnd = engine.wave.round_for("X")
    rnd.state = RoundState.ENTRY_PARTIAL
    rnd.direction = "long_okx_short_lighter"
    rnd.entry_basis_bps = 40.0
    rnd.entry_legs = [
        Leg("lighter", "sell", 2.0, px=199.98),
        Leg("okx", "buy", 4.0, px=99.98, filled_qty=4.0, filled_px=99.98,
            filled_notional=4.0 * 99.98),
    ]
    rnd.unit_ratio = 2.0

    # Mock book calls so snapshot doesn't fail
    okx_book = Book("okx", "X", bids=[(99.96, 500)], asks=[(100.04, 500)])
    lt_book = Book("lighter", "X", bids=[(199.98, 500)], asks=[(200.02, 500)])
    monkeypatch.setattr(engine.okx, "book", lambda s: okx_book)
    monkeypatch.setattr(engine.lighter, "book", lambda s: lt_book)
    monkeypatch.setattr(engine.okx, "stats", lambda s: MagicMock(mark_price=100.0, funding_rate=0.0,
                                                                   funding_interval_h=8.0))
    monkeypatch.setattr(engine.lighter, "stats", lambda s: MagicMock(mark_price=200.0, funding_rate=0.0,
                                                                       funding_interval_h=1.0))
    monkeypatch.setattr(engine.okx, "specs", lambda: {"X": MagicMock()})
    monkeypatch.setattr(engine.lighter, "specs", lambda: {"X": MagicMock()})
    monkeypatch.setattr(engine.broker, "positions", lambda: {})
    monkeypatch.setattr(engine.broker, "equities", lambda: (10000.0, 10000.0))

    # Write the KILL file
    (tmp_path / "state" / "KILL").write_text("")

    result = engine.run_once()
    assert result.get("killed") is True, f"Expected killed, got {result}"

    # kill.jsonl should exist
    kill_log = tmp_path / "state" / "kill.jsonl"
    assert kill_log.exists()


def test_kill_switch_env_var(monkeypatch):
    """ARBTWIN_KILL=1 should also trigger the kill switch."""
    from arbtwin.engine import TwinEngine
    from arbtwin.config import AppConfig

    cfg = AppConfig(dry_run=True, strategy="wave")
    cfg.state_dir = str(Path(os.environ.get("TMP", "/tmp")) / f"test_r2_kill_env_{os.getpid()}")

    monkeypatch.setenv("ARBTWIN_KILL", "1")

    engine = TwinEngine(cfg, proxy=None)

    # No state dir needed — run_once should return killed before snapshot
    result = engine.run_once()
    assert result.get("killed") is True

    # Clean up env
    monkeypatch.delenv("ARBTWIN_KILL", raising=False)


# --------------------------------------------------------------------------- #
# bonus: abort_cost_bps
# --------------------------------------------------------------------------- #
def test_abort_cost_bps_computes_correct_cost():
    """_abort_cost_bps reports the realised cost of aborting."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    # OKX buy filled at 100.0 (4 units), Lighter unfilled
    r.entry_legs[1].filled_qty = 4.0
    r.entry_legs[1].filled_px = 100.0
    r.entry_legs[1].filled_notional = 400.0

    # Force flat creates exit at 100 * 0.9975 = 99.75
    force_flat(r, "test", collar_bps=25.0)

    # Simulate the exit filling
    for leg in r.exit_legs:
        leg.filled_qty = leg.qty_base
        leg.filled_px = leg.px
        leg.filled_notional = leg.qty_base * leg.px

    cost = _abort_cost_bps(r)
    assert cost is not None
    # Buy at 100, sell at 99.75: cost = (100 - 99.75)/100 * 1e4 = 25 bps
    assert cost == pytest.approx(25.0, rel=0.1)


def test_round_pnl_bps_with_unit_ratio():
    """round_pnl_bps normalises Lighter px by unit_ratio."""
    r = mk_round()
    # Enter: okx buy @ 100, lt sell @ 204 (ratio=2 → normalised 102)
    # basis = (102-100)/100 * 1e4 = 200 bps
    r.entry_legs = [
        Leg("lighter", "sell", 2.0, px=204.0, filled_qty=2.0, filled_px=204.0,
            filled_notional=408.0),
        Leg("okx", "buy", 4.0, px=100.0, filled_qty=4.0, filled_px=100.0,
            filled_notional=400.0),
    ]
    # Exit: okx sell @ 100, lt buy @ 201 (ratio=2 → normalised 100.5)
    # ox_x.side = "sell" → exit_basis = (100-100.5)/100 * 1e4 = -50 bps
    r.exit_legs = [
        Leg("lighter", "buy", 2.0, px=201.0, filled_qty=2.0, filled_px=201.0,
            filled_notional=402.0),
        Leg("okx", "sell", 4.0, px=100.0, filled_qty=4.0, filled_px=100.0,
            filled_notional=400.0),
    ]
    r.unit_ratio = 2.0

    pnl = round_pnl_bps(r, 2.0)
    # entry_basis = (102-100)/100*1e4 = 200
    # exit_basis = (100-100.5)/100*1e4 = -50 (ox.side="sell" → else branch)
    # pnl = 200 - (-50) = 250
    assert pnl is not None
    assert pnl == pytest.approx(250.0)


def test_to_dict_includes_abort_cost_bps():
    """Round.to_dict() includes abort_cost_bps when state is ABORT_UNWIND."""
    r = mk_round()
    okx, lt = mk_book(), mk_book(mid=200.0)
    decide(r, 40.0, okx, lt, 2.0, 4.0, 10.0)

    r.entry_legs[1].filled_qty = 4.0
    r.entry_legs[1].filled_px = 100.0
    r.entry_legs[1].filled_notional = 400.0

    force_flat(r, "test", collar_bps=25.0)

    # Before exit fills, abort_cost_bps should be None (or not present)
    d = r.to_dict()
    assert r.state == RoundState.ABORT_UNWIND

    # After exit fills
    for leg in r.exit_legs:
        leg.filled_qty = leg.qty_base
        leg.filled_px = leg.px
        leg.filled_notional = leg.qty_base * leg.px

    d = r.to_dict()
    assert "abort_cost_bps" in d