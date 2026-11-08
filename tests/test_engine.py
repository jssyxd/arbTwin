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
    assert engine.exits.open_tickets(), "one-sided fill must be queued for passive exit"
    assert any(a["level"] == "critical" for a in engine.alerts)


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