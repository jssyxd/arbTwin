"""Anchor behaviour + causal replay guarantees."""

from __future__ import annotations

import datetime as dt

import pytest

from arbtwin.anchor import AnchorBook, AnchorConfig, Session
from arbtwin.backtest.replay import replay
from arbtwin.config import BandParams


# --------------------------------------------------------------------------- #
# anchor
# --------------------------------------------------------------------------- #
def test_anchor_prefers_tradfi_when_session_open():
    session = Session(kind="us_equity")
    anchor = AnchorBook(session=session)
    # 2026-11-06 (Friday) 15:00 UTC == 10:00 ET, inside the session
    ts = dt.datetime(2026, 11, 6, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    anchor.observe_tradfi(101.0, ts)
    d = anchor.decide(ts, okx_index=100.0, lighter_index=100.2)
    assert d.source == "tradfi" and d.px == 101.0 and not d.frozen


def test_anchor_freezes_outside_session():
    session = Session(kind="us_equity")
    anchor = AnchorBook(session=session)
    ts_closed = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.timezone.utc).timestamp()  # Saturday
    d = anchor.decide(ts_closed, okx_index=100.0, lighter_index=100.1)
    assert d.frozen and d.source == "frozen"


def test_anchor_refuses_when_indices_disagree():
    anchor = AnchorBook(AnchorConfig(index_agreement_bps=20.0), Session(kind="crypto"))
    d = anchor.decide(1_000_000.0, okx_index=100.0, lighter_index=101.0)   # 100 bps apart
    assert d.source == "none" and not d.usable


def test_anchor_uses_median_when_close():
    anchor = AnchorBook(AnchorConfig(index_agreement_bps=50.0), Session(kind="crypto"))
    d = anchor.decide(1_000_000.0, okx_index=100.0, lighter_index=100.2)
    assert d.source == "venue_index_median"
    assert d.px == pytest.approx(100.1)


def test_session_hours():
    s = Session(kind="us_equity")
    open_ts = dt.datetime(2026, 11, 6, 15, 0, tzinfo=dt.timezone.utc).timestamp()   # Fri 10:00 ET
    closed_ts = dt.datetime(2026, 11, 6, 22, 0, tzinfo=dt.timezone.utc).timestamp()  # Fri 17:00 ET
    assert s.is_open(open_ts) and not s.is_open(closed_ts)
    assert s.is_open(dt.datetime(2026, 11, 7, 15, 0, tzinfo=dt.timezone.utc).timestamp()) is False


# --------------------------------------------------------------------------- #
# replay: causality
# --------------------------------------------------------------------------- #
def _triangle_series(n: int = 400) -> tuple[dict[int, float], dict[int, float]]:
    """Price path with a repeating, mean-reverting basis of +-80 bps."""
    okx, lt = {}, {}
    for i in range(n):
        px = 100.0 * (1 + 0.002 * (i % 40) / 40)
        basis = 80.0 if (i // 20) % 2 == 0 else -80.0
        okx[i * 3_600_000] = px
        lt[i * 3_600_000] = px * (1 + basis / 1e4)
    return okx, lt


def test_replay_finds_the_planted_edge():
    okx, lt = _triangle_series()
    res = replay("T", okx, lt, 1.0, fee_round_trip_bps=10.0, latency_bps=0.5,
                 slippage_bps=1.0, lookback=48, max_hold_bars=48)
    assert res.n_bars == len(okx)
    assert len(res.trades) > 5
    assert res.total_net_bps > 0
    # never negative by construction? no: costs can exceed a small move, so we
    # only assert that the average trade clears zero on this planted series
    assert res.avg_net_bps > 0


def test_replay_is_blind_when_fees_exceed_the_move():
    okx, lt = _triangle_series()
    res = replay("T", okx, lt, 1.0, fee_round_trip_bps=400.0, latency_bps=50.0,
                 slippage_bps=20.0, lookback=48)
    assert res.total_net_bps <= 0


def test_replay_rejects_uncalibrated_ratio():
    okx, lt = _triangle_series(50)
    res = replay("T", okx, lt, 0.0, fee_round_trip_bps=10.0)
    assert res.n_bars == 0 and not res.trades


def test_replay_entry_never_uses_its_own_bar():
    # a single spike at bar 60 must not be tradeable at bar 60 itself: the trade
    # is filled at bar i+1, so a one-bar spike produces no entry at the spike price
    okx = {i * 3_600_000: 100.0 for i in range(200)}
    lt = dict(okx)
    lt[60 * 3_600_000] = 100.0 * 1.02      # +200 bps for exactly one hour
    res = replay("SPIKE", okx, lt, 1.0, fee_round_trip_bps=5.0, latency_bps=0.0,
                 slippage_bps=0.0, lookback=24, max_hold_bars=24)
    for t in res.trades:
        assert t.entry_ts != 60 * 3_600_000   # never filled on the spike bar itself