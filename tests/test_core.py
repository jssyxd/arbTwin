"""Unit tests for the pure layers: no network, no credentials, no clock surprises."""

from __future__ import annotations

import math

import pytest

from arbtwin.band import entry_band_bps, exit_band_bps, max_position_usd, sigma_1h_bps
from arbtwin.calibration import calibrate, percentile, unit_ratio_for
from arbtwin.config import BandParams
from arbtwin.exitqueue import ExitQueue, ExitTicket
from arbtwin.fees import FeeBook, FeeSchedule, build_fee_book
from arbtwin.issuer_risk import build_card
from arbtwin.manipulation import profile
from arbtwin.mechanics import MechanicsBook, VenueMechanics, cascade_proxy, evaluate_venue, latency_cost_bps
from arbtwin.models import Book, MarketSpec, MarketStats
from arbtwin.spread import build_plan, compute_row


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def mk_book(venue: str, symbol: str, bid: float, ask: float, size: float = 10.0,
            levels: int = 5, step_bps: float = 5.0) -> Book:
    bids = [(bid * (1 - i * step_bps / 1e4), size) for i in range(levels)]
    asks = [(ask * (1 + i * step_bps / 1e4), size) for i in range(levels)]
    return Book(venue, symbol, bids=bids, asks=asks)


def mk_spec(venue: str, symbol: str, maker: float = 0.0, taker: float = 0.0,
            imf: float = 0.05, oi_limit: float = 1e7) -> MarketSpec:
    return MarketSpec(venue=venue, symbol=symbol, market_ref="1", price_decimals=2, size_decimals=3,
                      min_base=0.001, min_quote=10.0, maker_fee_bps=maker, taker_fee_bps=taker,
                      initial_margin_frac=imf, oi_limit_usd=oi_limit, max_leverage=1 / imf)


def mk_stats(venue: str, symbol: str, mark: float = 100.0, index: float = 100.0,
             oi_usd: float = 1e5, oi_limit: float = 1e7, funding: float = 0.0,
             interval_h: float = 8.0) -> MarketStats:
    return MarketStats(venue=venue, symbol=symbol, mark_price=mark, index_price=index,
                       oi_usd=oi_usd, oi_limit_usd=oi_limit, funding_rate=funding,
                       funding_interval_h=interval_h)


# --------------------------------------------------------------------------- #
# calibration: the ANTHROPIC lesson
# --------------------------------------------------------------------------- #
def test_calibration_accepts_same_instrument():
    okx = {i: 100.0 * (1 + 0.001 * i) for i in range(100)}
    lt = {i: okx[i] * 1.0003 for i in range(100)}
    res = calibrate("AAPL", okx, lt)
    assert res.verdict == "same_instrument"
    assert res.tradable and abs(res.ratio - 1) < 0.01


def test_calibration_accepts_stable_renominated_contract():
    okx = {i: 0.00002 * (1 + 0.002 * i) for i in range(100)}
    lt = {i: okx[i] * 1000.0 for i in range(100)}
    res = calibrate("1000SHIB", okx, lt)
    assert res.verdict == "ratio_stable"
    assert res.ratio == pytest.approx(1000.0, rel=1e-6)


def test_calibration_rejects_drifting_premium():
    # Lighter walks from 10.0x to 11.5x while OKX moves independently: two products
    okx = {i: 100.0 * (1 + 0.01 * math.sin(i / 3)) for i in range(120)}
    lt = {i: okx[i] * (10.0 + 0.012 * i) for i in range(120)}
    res = calibrate("ANTHROPIC", okx, lt)
    assert res.verdict == "reject_divergent"
    assert res.cv > 0.005


def test_calibration_needs_enough_overlap():
    res = calibrate("X", {1: 1.0, 2: 1.0}, {1: 1.0, 2: 1.0})
    assert res.verdict == "insufficient_data"
    assert not res.tradable


def test_unit_ratio_override_wins():
    assert unit_ratio_for({"unit_ratio_override": 12.7}, None) == 12.7
    assert unit_ratio_for({}, None) == 0.0


# --------------------------------------------------------------------------- #
# band: degenerate branches
# --------------------------------------------------------------------------- #
def test_band_degenerate_zero_vol_is_fee_driven():
    b = entry_band_bps(0.5, fee_bps=20.0, params=BandParams())
    assert b == pytest.approx(30.0)          # 20 * 1.5


def test_band_scales_with_vol_and_is_capped():
    low = entry_band_bps(10.0, fee_bps=10.0)
    high = entry_band_bps(60.0, fee_bps=10.0)
    assert high > low
    assert entry_band_bps(10_000.0, fee_bps=10.0) <= BandParams().cap_bps


def test_exit_band_is_a_fraction_of_entry():
    assert exit_band_bps(100.0) < 100.0
    assert exit_band_bps(100.0) >= BandParams().floor_bps * 0.5


def test_sigma_and_position_sizing():
    rets = [0.001, -0.001, 0.002, -0.002]
    assert sigma_1h_bps(rets) == pytest.approx(15.81, rel=1e-3)
    # margin budget binds first
    assert max_position_usd(1000, 10, 0.35, 1e9) == pytest.approx(min(3500, 3000))
    # depth binds
    assert max_position_usd(1e6, 10, 0.35, 1000, depth_pct=0.1) == pytest.approx(100)


# --------------------------------------------------------------------------- #
# fees
# --------------------------------------------------------------------------- #
def test_fee_model_order_type_and_incentive():
    s = FeeSchedule("lighter", maker_bps=0.0, taker_bps=0.0, incentive_value_bps=1.5)
    assert s.effective_bps("post_only") == pytest.approx(-1.5)
    s2 = FeeSchedule("okx", maker_bps=2.0, taker_bps=5.0, overrides={"ioc": 6.0})
    assert s2.effective_bps("ioc") == 6.0
    assert s2.hurdle_bps("post_only", 2.0) == pytest.approx(4.0)


def test_round_trip_cost_is_conservative():
    book = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    # open: lighter post-only 0 + okx ioc 5 = 5
    # close: okx exit max(5, 2)=5 + lighter passive exit max(0, 0)=0 -> 5
    assert book.round_trip_bps("lighter", "okx") == pytest.approx(10.0)


def test_build_fee_book_from_config():
    cfg = {"venues": {"okx": {"fee": {"maker_bps": 1.0, "taker_bps": 3.0}},
                      "lighter": {"fee": {"maker_bps": 0.0, "taker_bps": 0.0}}}}
    book = build_fee_book(cfg)
    assert book.okx.taker_bps == 3.0 and book.lighter.maker_bps == 0.0


# --------------------------------------------------------------------------- #
# mechanics gates
# --------------------------------------------------------------------------- #
def test_mechanics_blocks_thin_book_and_stale_mark_window():
    mech = VenueMechanics("lighter", mark_window_s=300, min_depth_usd_1pct=50_000)
    book = mk_book("lighter", "AAPL", 100, 100.1, size=1.0)      # tiny book
    stats = mk_stats("lighter", "AAPL")
    v = evaluate_venue(mech, stats, book)
    assert not v.allow
    assert any("depth" in r for r in v.reasons)


def test_mechanics_blocks_near_mark_cap():
    mech = VenueMechanics("x", mark_cap_px=110.0, min_depth_usd_1pct=100)
    book = mk_book("x", "S", 105, 105.1, size=1000)
    stats = mk_stats("x", "S", mark=105, index=105)
    v = evaluate_venue(mech, stats, book)
    assert not v.allow
    assert any("cap" in r for r in v.reasons)


def test_cascade_proxy_flags_oi_vs_depth():
    book = mk_book("x", "S", 100, 100.1, size=1)      # ~$500 of depth
    stats = mk_stats("x", "S", oi_usd=1e6)
    assert cascade_proxy(stats, book) > 6


def test_latency_cost_is_diffusive():
    assert latency_cost_bps(0, 300) == 0.0
    assert latency_cost_bps(50, 0) == 0.0
    assert latency_cost_bps(50, 3_600_000) == pytest.approx(50.0)
    assert latency_cost_bps(50, 300) < latency_cost_bps(50, 3000)


def test_mechanics_blocks_liq_too_close():
    mech = VenueMechanics("okx", min_depth_usd_1pct=100)
    book = mk_book("okx", "S", 100, 100.1, size=1000)
    stats = mk_stats("okx", "S")
    from arbtwin.models import VenuePosition

    pos = VenuePosition("okx", "S", qty_base=1.0, avg_entry=100.0, liquidation_price=90.0)
    limits = type("L", (), {"min_liq_distance_pct": 0.18, "max_book_age_s": 5.0})()
    v = evaluate_venue(mech, stats, book, pos, limits)
    assert not v.allow


# --------------------------------------------------------------------------- #
# spread matrix
# --------------------------------------------------------------------------- #
def test_row_direction_and_edge_sign():
    okx_book = mk_book("okx", "AAPL", 100.0, 100.05, size=100)
    lt_book = mk_book("lighter", "AAPL", 100.5, 100.55, size=100)   # Lighter rich by ~45bps
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    row = compute_row("AAPL", okx_book, lt_book, 1.0, fees, sigma_1h_bps=20.0, latency_ms=300,
                      slippage_bps=1.0)
    assert row.basis_bps > 0
    assert row.direction == "long_okx_short_lighter"
    assert row.executable_edge_bps > 0
    assert row.net_edge_bps == pytest.approx(
        row.executable_edge_bps - row.fee_bps - row.latency_cost_bps - row.slippage_bps)


def test_row_respects_unit_ratio():
    okx_book = mk_book("okx", "SHIB", 0.00002, 0.0000201, size=1e9)
    lt_book = mk_book("lighter", "1000SHIB", 0.02, 0.0201, size=1e6)   # 1000x denom
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    row = compute_row("1000SHIB", okx_book, lt_book, 1000.0, fees, sigma_1h_bps=20.0)
    assert abs(row.basis_bps) < 5      # normalised, so no fake 9900 bps premium


def test_row_blocks_without_ratio():
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    row = compute_row("X", mk_book("okx", "X", 1, 1.1), mk_book("lighter", "X", 1, 1.1), 0.0, fees, 10.0)
    assert row.action == "BLOCK"


def test_plan_legs_are_opposite_and_sized_by_ratio():
    okx_book = mk_book("okx", "AAPL", 100.0, 100.05, size=100)
    lt_book = mk_book("lighter", "AAPL", 100.5, 100.55, size=100)
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    row = compute_row("AAPL", okx_book, lt_book, 1.0, fees, 20.0)
    plan = build_plan(row, okx_book, lt_book, 1.0, notional_usd=1000)
    assert plan.leading.venue == "lighter" and plan.hedge.venue == "okx"
    assert plan.leading.side != plan.hedge.side
    assert plan.leading.notional_usd == pytest.approx(1000, rel=0.01)
    assert plan.hedge.notional_usd == pytest.approx(1000, rel=0.01)


# --------------------------------------------------------------------------- #
# manipulation / issuer risk / exits
# --------------------------------------------------------------------------- #
def test_thin_book_raises_band_multiplier():
    thin = mk_book("lighter", "S", 100, 100.1, size=1)
    prof = profile(thin, "S", "lighter")
    assert prof.thin_reference
    assert prof.band_multiplier > 1


def test_deep_book_is_not_flagged():
    deep = mk_book("lighter", "S", 100, 100.1, size=10_000)
    prof = profile(deep, "S", "lighter")
    assert not prof.thin_reference
    assert prof.band_multiplier == 1.0


def test_issuer_card_hard_flag_vetoes():
    spec = mk_spec("lighter", "X")
    stats = mk_stats("lighter", "X")
    card = build_card("X", [spec], [stats], {"X": {"flags": ["pre_ipo_claim"]}})
    assert not card.allow
    assert card.size_factor == 0.0


def test_issuer_card_soft_flags_shrink_size():
    spec = mk_spec("lighter", "X", imf=0.2)
    stats = mk_stats("lighter", "X", funding=0.002, interval_h=1.0)   # ~1752% APR
    card = build_card("X", [spec], [stats], {"X": {"flags": ["thin_float"]}})
    assert card.allow
    assert 0.0 < card.size_factor < 1.0


def test_exit_queue_is_patient_then_escalates():
    q = ExitQueue()
    t = q.add(ExitTicket(symbol="AAPL", venue="lighter", side="sell", qty_base=1.0, remaining=1.0,
                         deadline_s=900))
    book = mk_book("lighter", "AAPL", 100.0, 100.5)
    order = q.plan(t, book)
    assert order.tif == "post_only" and order.side == "sell" and order.px <= 100.0
    t.created_at -= 880          # nearly out of patience
    escalated = q.plan(t, book)
    assert escalated.tif == "ioc"
    t.forced = True
    forced = q.plan(t, book)
    # no market orders in this strategy: a forced exit is an aggressive limit
    assert forced.order_type == "limit" and forced.tif == "ioc"
    assert forced.px < book.bid           # crosses the touch, collared downwards


def test_exit_queue_accumulates_same_ticket():
    q = ExitQueue()
    a = q.add(ExitTicket(symbol="S", venue="okx", side="buy", qty_base=1, remaining=1))
    b = q.add(ExitTicket(symbol="S", venue="okx", side="buy", qty_base=2, remaining=2))
    assert a is b and a.remaining == 3

# --------------------------------------------------------------------------- #
# depth coverage: a level cap must never masquerade as measured depth
# --------------------------------------------------------------------------- #
def _dense_book(tick_bps: float, levels: int, size: float = 10.0) -> Book:
    return Book("okx", "X",
                bids=[(100.0 - 100.0 * tick_bps / 1e4 * i, size) for i in range(levels)],
                asks=[(100.0 + 100.0 * tick_bps / 1e4 * i, size) for i in range(levels)])


def test_depth_covered_flags_truncation():
    dense = _dense_book(0.013, 400)          # BTC-like: reach ~5bps only
    usd, covered, complete = dense.depth_covered(100.0)
    assert not complete
    assert 4.0 <= covered <= 6.0
    assert usd > 0

    coin = _dense_book(0.29, 400)            # AAPL-like: 400 levels reach ~116bps
    usd2, covered2, complete2 = coin.depth_covered(100.0)
    assert complete2 and covered2 == pytest.approx(100.0)
    # the truncated BTC book can still be *larger* in raw notional (all 400
    # levels sit inside 5 bps) — which is exactly why raw "1% depth" is not a
    # comparable number across tick regimes
    assert usd > 0 and usd2 > 0


def test_depth_density_is_scale_free():
    btc, _, _ = _dense_book(0.013, 400).depth_density_usd_per_bps()
    aapl, _, _ = _dense_book(0.29, 400).depth_density_usd_per_bps()
    # a dense tick packs many more levels into each bp, so its notional-per-bp
    # is larger even though its raw 1%-band notional is truncated
    assert btc > aapl


def test_gate_uses_density_not_truncated_notional():
    mech = VenueMechanics("okx", min_depth_usd_1pct=50_000, min_move_cost_usd=1.0,
                          mark_divergence_max_bps=1e6)
    # BTC-like truncation: raw "1% depth" looks small but density is fine
    btc = _dense_book(0.013, 400, size=200.0)
    stats = mk_stats("okx", "BTC", mark=100, index=100, oi_usd=1e3, oi_limit=1e9)
    v = evaluate_venue(mech, stats, btc)
    assert v.metrics["depth_complete"] is False
    assert v.metrics["depth_density_usd_per_bps"] > 500
    assert v.allow, v.reasons


# --------------------------------------------------------------------------- #
# funding unit conventions (this bit was wrong once: 100x error)
# --------------------------------------------------------------------------- #
def test_lighter_funding_percent_per_hour_is_normalised():
    from arbtwin.venues.lighter import LighterVenue

    v = LighterVenue()                      # no network touched here
    v._market_to_symbol[1] = "BTC"
    v._stats["BTC"] = MarketStats("lighter", "BTC", 1, 1, oi_limit_usd=1.0e9)
    v._ingest({"channel": "market_stats:1",
               "market_stats": {"symbol": "BTC", "mark_price": "75904.5", "index_price": "75907.3",
                                "current_funding_rate": "0.0012", "open_interest_usd": "330000000"}})
    st = v._stats["BTC"]
    assert st.funding_rate == pytest.approx(0.000012)          # 0.0012% / 100
    assert st.funding_apr_pct == pytest.approx(10.512, rel=1e-3)     # ~10.5% APR


def test_okx_funding_fraction_per_8h_annualises_sanely():
    st = MarketStats("okx", "BTC", 1, 1, funding_rate=0.0001, funding_interval_h=8.0)
    assert st.funding_apr_pct == pytest.approx(10.95, rel=1e-3)


def test_maker_round_trip_uses_passive_exit():
    book = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    # maker/maker both sides: 2 + 2 = 4 bps (not 7 with a taker exit)
    assert book.round_trip_bps("lighter", "okx", "post_only", "post_only",
                               close_type="post_only") == pytest.approx(4.0)
    # hedge_now keeps the conservative taker-exit assumption
    assert book.round_trip_bps("lighter", "okx") == pytest.approx(10.0)


def test_gross_trigger_semantics():
    okx_book = mk_book("okx", "AAPL", 100.0, 100.02, size=100)
    lt_book = mk_book("lighter", "AAPL", 100.14, 100.16, size=100)   # ~14 bps rich
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    hit = compute_row("AAPL", okx_book, lt_book, 1.0, fees, sigma_1h_bps=20.0,
                      band_override_bps=12.0)
    assert hit.action == "MAKER" and hit.entry_band_bps == pytest.approx(12.0)
    miss = compute_row("AAPL", okx_book, mk_book("lighter", "AAPL", 100.05, 100.07, size=100),
                       1.0, fees, sigma_1h_bps=20.0, band_override_bps=12.0)
    assert miss.action == "WAIT"


def test_lighter_split_keys_route_ioc_to_the_taker_key():
    from arbtwin.config import LighterCredentials
    from arbtwin.venues.lighter import LighterVenue

    creds = LighterCredentials(api_private_key="aa", api_key_index=4,
                               api_private_key_taker="bb", api_key_index_taker=5,
                               account_index=767659)
    v = LighterVenue(creds)
    created = []

    class FakeClient:
        def __init__(self, base_url, account_index, api_private_key, api_key_index):
            created.append(api_key_index)

    import types, sys
    mod = types.ModuleType("arbtwin.venues.lighter_trade")
    mod.LighterTradeClient = FakeClient
    sys.modules["arbtwin.venues.lighter_trade"] = mod
    try:
        assert v._trader(taker=False) is v._trader(taker=False)      # cached per slot
        assert v._trader(taker=True) is not v._trader(taker=False)
        assert created == [4, 5]
    finally:
        sys.modules.pop("arbtwin.venues.lighter_trade", None)


def test_lighter_without_taker_key_falls_back_to_the_single_key():
    from arbtwin.config import LighterCredentials
    from arbtwin.venues.lighter import LighterVenue

    creds = LighterCredentials(api_private_key="aa", api_key_index=4, account_index=1)
    v = LighterVenue(creds)
    created = []

    class FakeClient:
        def __init__(self, base_url, account_index, api_private_key, api_key_index):
            created.append(api_key_index)

    import types, sys
    mod = types.ModuleType("arbtwin.venues.lighter_trade")
    mod.LighterTradeClient = FakeClient
    sys.modules["arbtwin.venues.lighter_trade"] = mod
    try:
        v._trader(taker=True)
        assert created == [4]          # no taker key configured -> single key
    finally:
        sys.modules.pop("arbtwin.venues.lighter_trade", None)


def test_live_okx_without_passphrase_is_fail_closed():
    """An empty passphrase must never look like a usable live credential."""
    from arbtwin.config import OkxCredentials

    assert OkxCredentials(api_key="k", api_secret="s", passphrase="").has_private is False
    assert OkxCredentials(api_key="k", api_secret="s", passphrase="p").has_private is True
    # and the engine's sizing refuses without equity from *both* venues
    from arbtwin.engine import Broker
    from arbtwin.venues.okx import OkxVenue
    from arbtwin.venues.lighter import LighterVenue

    okx = OkxVenue(OkxCredentials(api_key="k", api_secret="s", passphrase=""))
    # equity reads report 0 instead of guessing -> sizing returns 0 -> no orders,
    # and `place()` raises if anything ever tries to send
    assert okx.equity_usd() == 0.0
    from arbtwin.models import OrderRequest, MarketSpec
    spec = MarketSpec("okx", "X", "X-USDT-SWAP", 2, 3, 0.001, 0.0)
    with pytest.raises(Exception):
        okx.place(OrderRequest("okx", "X", "buy", 1.0, 100.0), spec)


def test_lighter_equity_prefers_account_totals():
    """`assets[].balance` reads 0.000000 on a funded account (margin_balance has
    the money) -- sizing against `balance` would silently never trade."""
    from arbtwin.venues.lighter import LighterVenue

    v = LighterVenue()
    v.creds.account_index = 767659

    class FakeResp:
        @staticmethod
        def read():
            import json as _j
            return _j.dumps({"accounts": [{
                "assets": [{"symbol": "USDC", "balance": "0.000000", "margin_balance": "27.629041"}],
                "collateral": "27.629041", "available_balance": "27.629041",
                "total_asset_value": "27.629041"}]}).encode()

    v._get = lambda path, params=None: __import__("json").loads(  # type: ignore[assignment]
        '{"accounts":[{"assets":[{"symbol":"USDC","balance":"0.000000","margin_balance":"27.629041"}],'
        '"collateral":"27.629041","available_balance":"27.629041","total_asset_value":"27.629041"}]}')
    assert v.equity_usd() == pytest.approx(27.629041)


def test_maker_edge_is_symmetric_in_basis_sign():
    """trigger uses abs(basis); the edge must too, else only one side ever trades."""
    fees = FeeBook(okx=FeeSchedule("okx", 2.0, 5.0), lighter=FeeSchedule("lighter", 0.0, 0.0))
    rich = compute_row("X", mk_book("okx", "X", 100.0, 100.02, size=100),
                       mk_book("lighter", "X", 100.14, 100.16, size=100), 1.0, fees,
                       sigma_1h_bps=20.0, band_override_bps=12.0)
    cheap = compute_row("X", mk_book("okx", "X", 100.0, 100.02, size=100),
                        mk_book("lighter", "X", 99.86, 99.88, size=100), 1.0, fees,
                        sigma_1h_bps=20.0, band_override_bps=12.0)
    assert rich.basis_bps > 0 and cheap.basis_bps < 0
    assert rich.maker_net_bps > 0 and cheap.maker_net_bps > 0
    assert abs(rich.maker_net_bps - cheap.maker_net_bps) < 1.0
    assert rich.action == "MAKER" and cheap.action == "MAKER"


def test_okx_book_sizes_are_converted_from_contracts(monkeypatch):
    """`/market/books` returns CONTRACTS. XAU ctVal=0.001 must divide depth by 1000."""
    from arbtwin.venues import okx as okx_mod
    from arbtwin.config import OkxCredentials
    from arbtwin.models import MarketSpec

    monkeypatch.setattr(okx_mod, "http_json",
                        lambda *a, **k: {"code": "0", "data": [{
                            "bids": [["4068.5", "48615", "0", "1"]],
                            "asks": [["4069.0", "1000", "0", "1"]]}]})
    v = okx_mod.OkxVenue(OkxCredentials())
    v._by_symbol["XAU"] = MarketSpec("okx", "XAU", "XAU-USDT-SWAP", 1, 0, 0.001, 0.0,
                                     contract_multiplier=0.001)
    book = v.book("XAU")
    assert book.bids[0][1] == pytest.approx(48.615)     # 48615 contracts x 0.001
    assert book.asks[0][1] == pytest.approx(1.0)
    assert book.depth_usd(100.0) < 1e6                   # not the 1000x-inflated number
