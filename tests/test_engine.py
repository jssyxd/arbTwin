"""Engine-level tests with fake venues: no network, deterministic books."""

from __future__ import annotations

import time

import pytest

from arbtwin.config import AppConfig, BandParams, EngineLimits
from arbtwin.engine import TwinEngine
from arbtwin.models import Book, Fill, MarketSpec, MarketStats, OrderRequest, VenuePosition
from arbtwin.venues.base import VenueAdapter


class FakeVenue(VenueAdapter):
    def __init__(self, name: str, bid: float, ask: float, size: float = 500.0,
                 maker: float = 0.0, taker: float = 0.0, oi: float = 1e6, oi_limit: float = 1e9):
        self.name = name
        self._bid, self._ask, self._size = bid, ask, size
        self._maker, self._taker = maker, taker
        self._oi, self._oi_limit = oi, oi_limit
        self.orders: list[OrderRequest] = []
        self.fail_orders = False

    def specs(self, symbols=None):
        spec = MarketSpec(venue=self.name, symbol="AAPL", market_ref="1", price_decimals=2,
                          size_decimals=3, min_base=0.001, min_quote=10.0,
                          maker_fee_bps=self._maker, taker_fee_bps=self._taker,
                          initial_margin_frac=0.05, oi_limit_usd=self._oi_limit, max_leverage=20.0)
        return {"AAPL": spec}

    def book(self, symbol: str, levels: int = 0) -> Book:
        return Book(self.name, symbol,
                    bids=[(self._bid * (1 - i * 5 / 1e4), self._size) for i in range(20)],
                    asks=[(self._ask * (1 + i * 5 / 1e4), self._size) for i in range(20)])

    def stats(self, symbol: str) -> MarketStats:
        mid = (self._bid + self._ask) / 2
        return MarketStats(self.name, symbol, mark_price=mid, index_price=mid,
                           oi_usd=self._oi, oi_limit_usd=self._oi_limit,
                           funding_rate=0.0, funding_interval_h=8.0)

    def positions(self):
        return {}

    def equity_usd(self) -> float:
        return 10_000.0

    def cancel_all(self, symbol: str) -> int:
        return 0

    def place(self, order: OrderRequest, spec: MarketSpec) -> Fill:
        if self.fail_orders:
            raise RuntimeError("venue rejected the order")
        self.orders.append(order)
        fee = spec.maker_fee_bps if order.tif == "post_only" else spec.taker_fee_bps
        return Fill(self.name, order.symbol, order.side, order.qty_base, order.px,
                    fee_bps=fee, order_id="fake")


def mk_cfg(**limits) -> AppConfig:
    cfg = AppConfig()
    cfg.dry_run = True
    cfg.state_dir = "state/test"
    cfg.limits = EngineLimits(**{**EngineLimits().__dict__, **limits})
    cfg.band = BandParams(floor_bps=5.0, degenerate_sigma_bps=1.0, k_sigma=1.0)
    cfg.venues = {
        "venues": {
            "okx": {"fee": {"maker_bps": 2.0, "taker_bps": 5.0},
                    "mechanics": {"min_depth_usd_1pct": 1000.0, "min_move_cost_usd": 1000.0}},
            "lighter": {"fee": {"maker_bps": 0.0, "taker_bps": 0.0},
                        "mechanics": {"min_depth_usd_1pct": 1000.0, "min_move_cost_usd": 1000.0,
                                      "taker_speed_bump_ms": 300}},
        }
    }
    cfg.symbols = [{"symbol": "AAPL", "unit_ratio": 1.0, "sigma_1h_bps": 40.0, "session": "crypto"}]
    return cfg


def mk_engine(lighter_bid: float = 101.2, lighter_ask: float = 101.25) -> tuple[TwinEngine, FakeVenue, FakeVenue]:
    okx = FakeVenue("okx", 100.0, 100.05, maker=2.0, taker=5.0)
    lt = FakeVenue("lighter", lighter_bid, lighter_ask, maker=0.0, taker=0.0)
    engine = TwinEngine(mk_cfg(), okx, lt)
    engine.load_universe()
    return engine, okx, lt


def test_snapshot_builds_rows_and_gates():
    engine, _, _ = mk_engine()
    snap = engine.snapshot()
    assert len(snap["rows"]) == 1
    row = snap["rows"][0]
    assert row.symbol == "AAPL"
    assert row.basis_bps > 0                      # Lighter rich
    assert row.direction == "long_okx_short_lighter"
    assert "depth_1pct_usd" in snap["gates"]["AAPL"]


def test_hedge_now_execution_opens_two_legs():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    snap["rows"][0].action = "ENTER"              # force the branch under test
    trades = engine.execute_best(snap)
    assert trades and trades[0]["status"] == "open"
    st = engine.states["AAPL"]
    # Lighter is rich -> sell Lighter, buy OKX
    assert st.side == "short_lighter"
    assert st.qty_lighter < 0 and st.qty_okx > 0
    assert engine.trades[-1]["status"] == "open"
    # exiting must queue positive quantities on both venues, sides flipped
    engine._open_exit(st, "test exit")
    tickets = engine.exits.open_tickets()
    assert len(tickets) == 2 and all(t.qty_base > 0 for t in tickets)


def test_failed_hedge_unwinds_leading_leg():
    engine, okx, lt = mk_engine()

    real_place = engine.broker.place
    seen: list[OrderRequest] = []

    def flaky(order, spec):
        seen.append(order)
        if order.venue == "okx":
            raise RuntimeError("boom")
        return real_place(order, spec)

    engine.broker.place = flaky  # type: ignore[assignment]
    snap = engine.snapshot()
    snap["rows"][0].action = "ENTER"
    out = engine.execute_best(snap)[0]
    assert out["status"] == "unwound"
    assert any(a["level"] == "critical" for a in engine.alerts)
    assert engine.states["AAPL"].side == "flat"
    # the unwind order must be reduce-only and on the leading venue
    unwind = seen[-1]
    assert unwind.reduce_only is True and unwind.venue == "lighter"


def test_dual_maker_quotes_then_fills_both_legs():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    row = snap["rows"][0]
    row.action = "MAKER"
    row.maker_net_bps, row.maker_band_bps = 30.0, 10.0
    out = engine.execute_best(snap)
    assert out[0]["status"] == "quoted"
    assert len(engine.maker_quotes) == 2
    # both quotes are passive
    assert all(q.placed_ts for q in engine.maker_quotes)
    # market trades through both quotes -> both fill -> position opens
    lt._bid, lt._ask = 101.30, 101.35      # through our Lighter sell @101.20
    okx._bid, okx._ask = 100.00, 100.02    # through our OKX buy @100.05
    engine._poll_maker(snap["rows"])
    st = engine.states["AAPL"]
    assert st.side in ("long_lighter", "short_lighter")
    assert engine.maker_quotes == []


def test_one_sided_maker_fill_goes_to_exit_queue():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    row = snap["rows"][0]
    row.action, row.maker_net_bps, row.maker_band_bps = "MAKER", 30.0, 10.0
    engine.execute_best(snap)
    quotes = {q.venue: q for q in engine.maker_quotes}
    # only the Lighter sell quote fills; OKX book stays away from its quote
    quotes["lighter"].filled = True
    quotes["lighter"].fill_ts = time.time() - 5.0     # older than the 800ms budget
    quotes["lighter"].fill_px = quotes["lighter"].px
    okx._bid, okx._ask = 101.0, 101.05
    engine._poll_maker(snap["rows"])
    assert engine.maker_quotes == []
    tickets = engine.exits.open_tickets()
    assert tickets, "one-sided fill must be queued for passive exit"
    # we sold Lighter -> the exit must buy it back, and nothing else is queued
    assert [t.venue for t in tickets] == ["lighter"]
    assert tickets[0].side == "buy" and tickets[0].qty_base > 0
    assert any(a["level"] == "critical" for a in engine.alerts)


def test_one_sided_fill_on_okx_exits_with_sell_side():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    row = snap["rows"][0]
    row.action, row.maker_net_bps, row.maker_band_bps = "MAKER", 30.0, 10.0
    engine.execute_best(snap)
    quotes = {q.venue: q for q in engine.maker_quotes}
    # only the OKX buy quote fills (Lighter book moves away)
    quotes["okx"].filled = True
    quotes["okx"].fill_ts = time.time() - 5.0
    quotes["okx"].fill_px = quotes["okx"].px
    lt._bid, lt._ask = 99.0, 99.05
    engine._poll_maker(snap["rows"])
    tickets = engine.exits.open_tickets()
    assert [t.venue for t in tickets] == ["okx"]
    assert tickets[0].side == "sell" and tickets[0].qty_base > 0


def test_sizing_respects_depth_and_hard_cap():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    row = snap["rows"][0]
    plan = engine._size_plan(row, engine.states["AAPL"])
    assert plan is not None
    # legs share one base quantity, so the leg notional differs from the cap by
    # exactly the basis (you sell the rich side at a higher price)
    cap = engine.cfg.limits.max_notional_usd
    assert plan.leading.notional_usd <= cap * (1 + abs(row.basis_bps) / 1e4) + 1e-6
    assert plan.hedge.notional_usd <= cap + 1e-6


def test_uncalibrated_symbol_is_refused():
    cfg = mk_cfg()
    cfg.symbols = [{"symbol": "AAPL", "unit_ratio": 0.0}]
    engine = TwinEngine(cfg, FakeVenue("okx", 100, 100.1), FakeVenue("lighter", 100, 100.1))
    assert engine.load_universe() == {}

def test_cli_refuses_live_run_without_the_flag(monkeypatch):
    """ARBTWIN_DRY_RUN=0 must be a hard stop, not a crash and not a live start."""
    from arbtwin import cli

    monkeypatch.setenv("ARBTWIN_DRY_RUN", "0")
    assert cli.main(["run", "--iterations", "1"]) == 2


def test_max_hold_forces_a_passive_exit():
    engine, okx, lt = mk_engine()
    snap = engine.snapshot()
    snap["rows"][0].action = "ENTER"
    engine.execute_best(snap)
    st = engine.states["AAPL"]
    assert st.side != "flat"
    st.entry_ts -= 5 * 3600          # older than max_hold_h (4h)
    actions = engine.manage_risk(engine.snapshot())
    queued = [a for a in actions if a.get("legs")]
    assert queued and queued[0]["legs"] == 2 and not queued[0]["forced"]
    assert "max hold" in queued[0]["exit_queued"]
    # in dry-run the passive exit fills against the touch on the same poll
    assert engine.states["AAPL"].side == "flat"


def test_band_override_is_honoured():
    cfg = mk_cfg()
    cfg.symbols = [{"symbol": "AAPL", "unit_ratio": 1.0, "sigma_1h_bps": 10.0,
                    "basis_sigma_bps": 2.0, "session": "crypto", "band_override_bps": 12.0}]
    engine = TwinEngine(cfg, FakeVenue("okx", 100.0, 100.05), FakeVenue("lighter", 101.2, 101.25))
    engine.load_universe()
    row = engine.snapshot()["rows"][0]
    assert row.entry_band_bps == pytest.approx(12.0)


# --------------------------------------------------------------------------- #
# wave mode: one round at a time, both legs to flat
# --------------------------------------------------------------------------- #
def mk_wave_cfg() -> AppConfig:
    cfg = mk_cfg()
    cfg.strategy = "wave"
    cfg.symbols = [{
        "symbol": "AAPL", "unit_ratio": 1.0, "sigma_1h_bps": 40.0, "basis_sigma_bps": 6.0,
        "session": "crypto", "entry_bps_p95": 14.0, "exit_bps_p50": 4.5,
        "max_unhedged_notional_usd": 10_000.0,
    }]
    return cfg


def _wave_step(engine):
    """One engine poll: re-price the matrix, then step the round."""
    return engine.wave_step(engine.snapshot())


def test_wave_round_runs_flat_to_flat_without_overlap():
    okx = FakeVenue("okx", 100.0, 100.05, size=500.0, maker=2.0, taker=5.0)
    lt = FakeVenue("lighter", 101.40, 101.45, size=500.0)      # ~140 bps rich
    engine = TwinEngine(mk_wave_cfg(), okx, lt)
    engine.load_universe()
    assert engine.wave is not None

    _wave_step(engine)
    rnd = engine.wave.round_for("AAPL")
    assert rnd.state.value == "entry_quoting"
    quoted = {q.venue: q for q in engine.wave.open_quotes("AAPL")}
    assert set(quoted) == {"okx", "lighter"}
    assert quoted["lighter"].side == "sell" and quoted["okx"].side == "buy"

    # only the Lighter leg fills: the market must trade *through* our sell
    sell_px = quoted["lighter"].px
    lt._bid, lt._ask = sell_px + 0.05, sell_px + 0.10
    _wave_step(engine)
    assert rnd.state.value == "entry_partial"
    still = {q.venue: q for q in engine.wave.open_quotes("AAPL")}
    assert "okx" in still and "lighter" not in still

    # OKX leg fills (ask trades below our bid) -> hedged
    buy_px = still["okx"].px
    okx._bid, okx._ask = buy_px - 0.10, buy_px - 0.05
    _wave_step(engine)
    assert rnd.state.value == "hedged"

    # basis converges to ~3 bps -> exit quotes, reduce_only, frozen prices
    okx._bid, okx._ask = 100.30, 100.32
    lt._bid, lt._ask = 100.33, 100.35
    _wave_step(engine)
    assert rnd.state.value in ("exit_quoting", "exit_partial"), rnd.state
    exits = engine.wave.open_quotes("AAPL")
    assert exits and all(q.reduce_only for q in exits)
    frozen = dict(rnd.frozen)

    # both exits trade through their frozen prices -> round complete
    lt._bid, lt._ask = frozen["lighter"] - 0.10, frozen["lighter"] - 0.05
    okx._bid, okx._ask = frozen["okx"] + 0.05, frozen["okx"] + 0.10
    _wave_step(engine)
    assert rnd.state.value == "flat"
    assert engine.wave.open_quotes("AAPL") == []


def test_wave_does_not_open_a_second_round_while_closing():
    okx = FakeVenue("okx", 100.0, 100.05, size=500.0, maker=2.0, taker=5.0)
    lt = FakeVenue("lighter", 101.40, 101.45, size=500.0)
    engine = TwinEngine(mk_wave_cfg(), okx, lt)
    engine.load_universe()
    _wave_step(engine)
    rnd = engine.wave.round_for("AAPL")

    sell_px = {q.venue: q.px for q in engine.wave.open_quotes("AAPL")}["lighter"]
    lt._bid, lt._ask = sell_px + 0.05, sell_px + 0.10
    _wave_step(engine)
    buy_px = {q.venue: q.px for q in engine.wave.open_quotes("AAPL")}["okx"]
    okx._bid, okx._ask = buy_px - 0.10, buy_px - 0.05
    _wave_step(engine)
    assert rnd.state.value == "hedged"

    before = rnd.round_id
    # basis dislocates wider again while still hedged: no new round may start
    lt._bid, lt._ask = 102.90, 102.95
    okx._bid, okx._ask = 101.00, 101.02
    _wave_step(engine)
    assert rnd.round_id == before
    assert rnd.state.value in ("hedged", "exit_quoting", "exit_partial")
def test_wave_does_not_open_a_second_round_while_closing():
    okx = FakeVenue("okx", 100.0, 100.05, size=500.0, maker=2.0, taker=5.0)
    lt = FakeVenue("lighter", 101.40, 101.45, size=500.0)
    engine = TwinEngine(mk_wave_cfg(), okx, lt)
    engine.load_universe()
    snap = engine.snapshot()
    engine.wave_step(snap)
    sell_px = {q.venue: q.px for q in engine.wave.open_quotes("AAPL")}["lighter"]
    lt._bid, lt._ask = sell_px + 0.05, sell_px + 0.10
    engine.wave_step(snap)
    buy_px = {q.venue: q.px for q in engine.wave.open_quotes("AAPL")}["okx"]
    okx._bid, okx._ask = buy_px - 0.10, buy_px - 0.05
    engine.wave_step(snap)
    rnd = engine.wave.round_for("AAPL")
    assert rnd.state.value == "hedged"
    before = rnd.round_id
    # basis dislocates wider while still hedged -> exit, not a new entry round
    lt._bid, lt._ask = 101.90, 101.95
    engine.wave_step(snap)
    assert rnd.round_id == before
    assert rnd.state.value in ("exit_quoting", "exit_partial", "hedged")


# --------------------------------------------------------------------------- #
# safety: closed session and orphaned positions must both block entries
# --------------------------------------------------------------------------- #
def test_closed_session_blocks_entries():
    cfg = mk_cfg()
    cfg.symbols = [{"symbol": "AAPL", "unit_ratio": 1.0, "sigma_1h_bps": 40.0,
                    "basis_sigma_bps": 6.0, "session": "us_equity",
                    "entry_bps_p95": 14.0, "exit_bps_p50": 4.5}]
    engine = TwinEngine(cfg, FakeVenue("okx", 100.0, 100.05), FakeVenue("lighter", 101.4, 101.45))
    engine.load_universe()
    st = engine.states["AAPL"]
    # force "closed": a Session whose window can never be open
    from arbtwin.anchor import Session
    st.session = Session(kind="us_equity", open_hhmm=(0, 0), close_hhmm=(0, 0))
    snap = engine.snapshot()
    assert snap["gates"]["AAPL"]["session_open"] is False
    assert snap["rows"][0].action == "BLOCK"
    assert any("session closed" in r for r in snap["rows"][0].reasons)


def test_orphan_position_blocks_wave_entries_until_flat():
    engine, okx, lt = mk_engine()
    engine.cfg.strategy = "wave"
    engine.load_universe()
    # simulate a restart with a live position and no in-memory round
    engine.orphans["AAPL"] = VenuePosition("lighter", "AAPL", qty_base=-30.0, avg_entry=101.0)
    out = engine.wave_step(engine.snapshot())
    assert out and out[0].get("blocked") == "orphan position on venue"
    assert engine.wave.open_quotes("AAPL") == []
    # once the venue reports flat, the block is lifted
    engine._update_orphans({"positions": {}})
    assert "AAPL" not in engine.orphans


def test_live_mode_is_refused_until_fill_reconciliation_exists():
    """Live fills are inferred from book crossings (dry-run proxy only). Until the
    venue order-state path exists, starting live must fail loudly, not invent PnL."""
    engine, _okx, _lt = mk_engine()
    engine.cfg.dry_run = False
    with pytest.raises(RuntimeError, match="ARBTWIN_FILL_SOURCE"):
        engine.run_forever(iterations=1)


def test_position_reconciliation_drives_a_live_round(tmp_path):
    """Live fills come from venue position deltas, not from the book."""
    from arbtwin.reconcile import PositionLedger

    engine, okx, lt = mk_engine()
    engine.cfg.dry_run = False
    engine.cfg.strategy = "wave"
    engine.load_universe()
    engine.cfg.fill_source = "positions"
    # a fresh ledger on a temp path so the test is hermetic
    engine.ledger = PositionLedger(str(tmp_path / "positions.json"))
    snap = engine.snapshot()
    engine.wave_step(snap)
    rnd = engine.wave.round_for("AAPL")
    assert rnd.state.value == "entry_quoting"

    # the venue reports a short on Lighter (sell leg filled) -> partial
    class P:
        def __init__(self, venue, symbol, qty, avg):
            self.venue, self.symbol, self.qty_base, self.avg_entry = venue, symbol, qty, avg
    engine.broker.positions = lambda: {"lighter:AAPL": P("lighter", "AAPL", -30.0, 101.0)}
    engine._reconcile_positions()
    assert rnd.state.value == "entry_partial"

    # the OKX hedge lands -> hedged
    engine.broker.positions = lambda: {"lighter:AAPL": P("lighter", "AAPL", -30.0, 101.0),
                                       "okx:AAPL": P("okx", "AAPL", 30.0, 100.0)}
    engine._reconcile_positions()
    assert rnd.state.value == "hedged"


def test_hedge_failures_bench_the_symbol():
    """Three failed hedges in a row must stop new entries for that symbol.

    Portable lesson from Cbb0fe's $1.1M gold incident: a leg that cannot be hedged
    keeps "correcting" itself into a directional bet.
    """
    engine, okx, lt = mk_engine()

    def flaky(order, spec):
        if order.venue == "okx":
            raise RuntimeError("hedge rejected")
        return real_place(order, spec)

    real_place = engine.broker.place
    engine.broker.place = flaky  # type: ignore[assignment]
    for _ in range(engine.cfg.limits.max_hedge_failures):
        engine.states["AAPL"].cooldown_until = 0.0
        snap = engine.snapshot()
        snap["rows"][0].action = "ENTER"
        engine.execute_best(snap)
    assert "AAPL" in engine.benched
    assert any(a["level"] == "critical" for a in engine.alerts)

    # benched symbol must not be quoted even when the row says ENTER
    engine.cfg.strategy = "wave"
    engine.broker.place = real_place  # type: ignore[assignment]
    engine.load_universe()
    out = engine.wave_step(engine.snapshot())
    assert any(str(x.get("blocked", "")).startswith("benched") for x in out)
    assert engine.wave.open_quotes("AAPL") == []

    # a successful hedge resets the streak
    engine.hedge_fail_streak["AAPL"] = 2
    engine.benched.clear()
    engine.states["AAPL"].cooldown_until = 0.0
    snap = engine.snapshot()
    snap["rows"][0].action = "ENTER"
    engine.execute_best(snap)
    assert engine.hedge_fail_streak.get("AAPL", 0) == 0
