"""Screening funnel end-to-end with fake venues: approve path + reject reasons."""

from __future__ import annotations

import math

from arbtwin.config import AppConfig, BandParams, EngineLimits
from arbtwin.models import Book, MarketSpec, MarketStats
from arbtwin.screening import build_symbols_yaml, classify_session, screen_symbols
from arbtwin.venues.base import VenueAdapter


class ScreenVenue(VenueAdapter):
    """Venue double with two symbols: WIDE (tradeable) and TIGHT (no edge)."""

    def __init__(self, name: str, offsets: dict[str, float], maker: float, taker: float,
                 size: float = 2_000.0):
        self.name = name
        self.offsets = offsets
        self._maker, self._taker, self._size = maker, taker, size

    def specs(self, symbols=None):
        out = {}
        for sym in self.offsets:
            out[sym] = MarketSpec(venue=self.name, symbol=sym, market_ref=sym,
                                  price_decimals=3, size_decimals=3, min_base=0.001,
                                  min_quote=10.0, maker_fee_bps=self._maker, taker_fee_bps=self._taker,
                                  initial_margin_frac=0.05, oi_limit_usd=1e9, max_leverage=20.0)
        return out

    def book(self, symbol: str, levels: int = 0) -> Book:
        mid = 100.0 * (1 + self.offsets[symbol] / 1e4)
        return Book(self.name, symbol,
                    bids=[(mid * (1 - i * 2 / 1e4), self._size) for i in range(50)],
                    asks=[(mid * (1 + (i + 1) * 2 / 1e4), self._size) for i in range(50)])

    def stats(self, symbol: str) -> MarketStats:
        mid = 100.0 * (1 + self.offsets[symbol] / 1e4)
        return MarketStats(self.name, symbol, mark_price=mid, index_price=mid,
                           oi_usd=1e6, oi_limit_usd=1e9, funding_rate=0.0,
                           funding_interval_h=1.0 if self.name == "lighter" else 8.0)

    def positions(self):
        return {}

    def equity_usd(self) -> float:
        return 10_000.0

    def place(self, order, spec):  # pragma: no cover - not used
        raise NotImplementedError

    def cancel_all(self, symbol: str) -> int:
        return 0


def _hourly(amplitudes: dict[str, float], n: int = 200) -> None:
    """Monkeypatch the history loader used by screening (no network in tests).

    The basis must *oscillate*: a constant offset is just a different unit and
    the calibration step would correctly absorb it into the ratio.
    """
    import arbtwin.screening as sc

    def fake_both(okx, lighter, symbol, days=7, use_cache=True):
        okx_series, lt_series = {}, {}
        amp = amplitudes[symbol]
        for i in range(n):
            # price vol >> basis vol, like real data (AAPL sigma_h 43bps vs basis 2.6bps)
            px = 100.0 * (1 + 0.02 * math.sin(i / 3.0)) * (1 + 0.0002 * i)
            basis = amp * math.sin(i / 40.0)
            okx_series[i * 3_600_000] = px
            lt_series[i * 3_600_000] = px * (1 + basis / 1e4)
        return okx_series, lt_series

    sc.both_hourly = fake_both  # type: ignore[assignment]


def mk_cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.limits = EngineLimits(min_depth_usd_1pct=1000.0, max_slippage_bps=2.0)
    cfg.band = BandParams(floor_bps=5.0, k_sigma=0.5, degenerate_sigma_bps=0.5)
    cfg.venues = {"venues": {
        "okx": {"fee": {"maker_bps": 2.0, "taker_bps": 5.0},
                "mechanics": {"min_depth_usd_1pct": 1000.0, "min_move_cost_usd": 1000.0}},
        "lighter": {"fee": {"maker_bps": 0.0, "taker_bps": 0.0},
                    "mechanics": {"min_depth_usd_1pct": 1000.0, "min_move_cost_usd": 1000.0}},
    }}
    cfg.risk = {"screening": {"min_depth_usd_1pct": 1000.0, "min_median_basis_bps": 1.0}}
    cfg.symbols = []
    return cfg


def test_funnel_approves_wide_basis_and_rejects_tight():
    offsets = {"WIDE": 0.0, "TIGHT": 0.0}
    _hourly({"WIDE": 60.0, "TIGHT": 2.0})
    okx = ScreenVenue("okx", offsets, maker=2.0, taker=5.0)
    lt = ScreenVenue("lighter", offsets, maker=0.0, taker=0.0)
    rows = screen_symbols(mk_cfg(), okx, lt, days=7, max_symbols=5)
    by_sym = {r.symbol: r for r in rows}

    assert by_sym["WIDE"].approved, by_sym["WIDE"].reject_reasons
    assert by_sym["WIDE"].unit_ratio > 0
    assert by_sym["WIDE"].preferred_mode in ("hedge_now", "dual_maker")
    assert not by_sym["TIGHT"].approved
    assert by_sym["TIGHT"].reject_reasons

    payload = build_symbols_yaml(rows)
    assert payload["universe"]["approved"] == 1
    entry = payload["approved"][0]
    assert entry["symbol"] == "WIDE"
    for key in ("unit_ratio", "sigma_1h_bps", "basis_sigma_bps", "band_bps", "session", "preferred_mode"):
        assert key in entry


def test_funnel_rejects_divergent_instrument():
    offsets = {"DIVERGE": 0.0}
    import arbtwin.screening as sc
    import arbtwin.calibration as cal

    def fake_both(okx, lighter, symbol, days=7, use_cache=True):
        okx_series, lt_series = {}, {}
        for i in range(200):
            px = 100.0 + math.sin(i / 5.0)
            okx_series[i * 3_600_000] = px
            lt_series[i * 3_600_000] = px * (10.0 + 0.02 * i)      # drifting ratio
        return okx_series, lt_series

    sc.both_hourly = fake_both  # type: ignore[assignment]
    okx = ScreenVenue("okx", offsets, 2.0, 5.0)
    lt = ScreenVenue("lighter", offsets, 0.0, 0.0)
    rows = screen_symbols(mk_cfg(), okx, lt, days=7, max_symbols=5)
    assert not rows[0].approved
    assert any("not one instrument" in r for r in rows[0].reject_reasons)


def test_session_classification():
    assert classify_session("ETH") == "crypto"
    assert classify_session("1000PEPE") == "crypto"
    assert classify_session("AAPL") == "us_equity"
    assert classify_session("HYUNDAIUSD") == "us_equity"