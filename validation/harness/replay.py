"""Deterministic fake-clock event loop for replaying book/tick feeds.

Reuses ideas from `arbtwin/backtest/replay.py` but extends them to:
- Feed-level replay: each event is a book update, trade, or timer tick with an
  explicit UTC ms timestamp; the clock advances event-by-event (fake-clock),
  not by wall time.
- Pluggable strategy callback: supply any callable `(clock_ms, book_okx,
  book_lighter, trade, state) -> dict` to drive your own logic.
- n_slices=1 vs sliced A/B: single-slice replay for baseline metrics;
  sliced A/B replay for comparing two parameter sets on the same data path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

import pandas as pd


# ── Event types ─────────────────────────────────────────────────────────────


class EventKind(str, Enum):
    BOOK = "book"
    TRADE = "trade"
    TIMER = "timer"


@dataclass
class Event:
    ts: int               # UTC ms (fake-clock)
    kind: EventKind
    venue: str             # "okx" | "lighter"
    symbol: str
    payload: dict = field(default_factory=dict)  # book {bids,asks} or trade {px,sz,side}
    recv_ts: int = 0
    venue_ts: int = 0


# ── Configuration ───────────────────────────────────────────────────────────


@dataclass
class ReplayConfig:
    symbol: str
    unit_ratio: float = 1.0            # Lighter base units per OKX base unit
    fee_bps: float = 4.0               # round-trip fee in bps
    entry_bps: float = 14.0
    exit_bps: float = 4.5
    entry_deadline_s: float = 5400.0
    exit_deadline_s: float = 3600.0
    max_hold_s: float = 14400.0        # 4 h
    n_slices: int = 1                  # 1 = single run, 2+ = A/B
    warmup_events: int = 100
    # Strategy callback
    strategy: Optional[Callable[..., dict]] = None


# ── Book state ──────────────────────────────────────────────────────────────


@dataclass
class VenueBook:
    venue: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    last_update_ts: int = 0

    @property
    def bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def mid(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.bids[0][0] + self.asks[0][0]) / 2.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        if not m:
            return float("inf")
        return (self.asks[0][0] - self.bids[0][0]) / m * 1e4

    def update(self, bids: list, asks: list, ts: int) -> None:
        self.bids = sorted(((px, sz) for px, sz in bids if sz > 0), key=lambda x: -x[0])
        self.asks = sorted(((px, sz) for px, sz in asks if sz > 0), key=lambda x: x[0])
        self.last_update_ts = ts

    def passive_px(self, side: str) -> float:
        return self.ask if side == "sell" else self.bid

    def marketable_px(self, side: str) -> float:
        return self.bid if side == "sell" else self.ask


# ── Default two-venue basis strategy ───────────────────────────────────────


def _basis_bps(book_okx: VenueBook, book_lt: VenueBook, ratio: float) -> float:
    """Basis = (Lighter_mid / ratio - OKX_mid) / OKX_mid * 1e4."""
    m_okx = book_okx.mid
    m_lt = book_lt.mid
    if not m_okx or not m_lt:
        return 0.0
    return (m_lt / ratio - m_okx) / m_okx * 1e4


def _default_strategy(
    clock_ms: int, book_okx: VenueBook, book_lt: VenueBook, trade: Optional[Event],
    state: dict, config: ReplayConfig,
) -> dict:
    """Minimal basis-entry/exit strategy returning 'entry' or 'exit' signals.

    Tracks a single position slot; enters when |basis| >= entry_bps + fee;
    exits when |basis| <= exit_bps or age >= max_hold.
    """
    basis = _basis_bps(book_okx, book_lt, config.unit_ratio)
    pos = state.get("position")

    if pos is None:
        if abs(basis) >= config.entry_bps + config.fee_bps:
            direction = "short_basis" if basis > 0 else "long_basis"
            state["position"] = {
                "entry_ms": clock_ms,
                "entry_basis": basis,
                "direction": direction,
            }
            return {"signal": "entry", "basis": basis, "direction": direction}
        return {"signal": "none", "basis": basis}

    held_ms = clock_ms - pos["entry_ms"]
    captured = (pos["entry_basis"] - basis) if pos["direction"] == "short_basis" else (basis - pos["entry_basis"])
    if captured >= config.exit_bps or held_ms >= config.max_hold_s * 1000:
        result = {
            "signal": "exit",
            "basis": basis,
            "entry_basis": pos["entry_basis"],
            "direction": pos["direction"],
            "held_ms": held_ms,
            "captured_bps": captured,
        }
        state["position"] = None
        return result
    return {"signal": "hold", "basis": basis}


# ── Replay outcomes ─────────────────────────────────────────────────────────


class ReplayOutcome(str, Enum):
    COMPLETE = "complete"          # reached end of events
    SIGNAL = "signal"              # strategy triggered a trade signal
    TIMEOUT = "timeout"            # replayed all events, partial position open


@dataclass
class ReplayResult:
    symbol: str
    n_events: int = 0
    n_book: int = 0
    n_trade: int = 0
    elapsed_real_s: float = 0.0
    elapsed_fake_s: float = 0.0     # (last_event_ts - first_event_ts) / 1000
    signals: list[dict] = field(default_factory=list)
    outcome: ReplayOutcome = ReplayOutcome.COMPLETE
    final_basis: float = 0.0
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "n_events": self.n_events,
            "n_book": self.n_book,
            "n_trade": self.n_trade,
            "elapsed_real_s": round(self.elapsed_real_s, 3),
            "elapsed_fake_s": round(self.elapsed_fake_s, 3),
            "n_signals": len(self.signals),
            "outcome": self.outcome.value,
            "final_basis": round(self.final_basis, 2),
            "signals": self.signals[-5:],  # last 5
        }


@dataclass
class SlicedReplayResult:
    symbol: str
    slice_a: ReplayResult = field(default_factory=lambda: ReplayResult(""))
    slice_b: Optional[ReplayResult] = None
    comparison: dict = field(default_factory=dict)


# ── Core replay loop ───────────────────────────────────────────────────────


def run_replay(
    events: list[Event],
    config: ReplayConfig,
    strategy: Optional[Callable[..., dict]] = None,
) -> ReplayResult:
    """Deterministic fake-clock replay over a sorted event list.

    Advances two venue books.  Calls strategy(clock_ms, book_okx, book_lt, trade, state, config)
    on each event.  `state` is a mutable dict the strategy can use for persistence.
    """
    strat = strategy or _default_strategy
    state: dict = {}
    book_okx = VenueBook("okx")
    book_lt = VenueBook("lighter")

    result = ReplayResult(symbol=config.symbol)
    t0 = time.monotonic()

    first_ts = events[0].ts if events else 0
    last_ts = first_ts

    for ev in events:
        last_ts = ev.ts
        if ev.kind == EventKind.BOOK:
            result.n_book += 1
            bids = ev.payload.get("bids", [])
            asks = ev.payload.get("asks", [])
            if ev.venue == "okx":
                book_okx.update(bids, asks, ev.ts)
            else:
                book_lt.update(bids, asks, ev.ts)
            # call strategy on book update
            if result.n_book > config.warmup_events:
                sig = strat(ev.ts, book_okx, book_lt, None, state, config)
                if sig.get("signal") not in (None, "none", "hold"):
                    result.signals.append(sig)
        elif ev.kind == EventKind.TRADE:
            result.n_trade += 1
            if result.n_trade > config.warmup_events:
                sig = strat(ev.ts, book_okx, book_lt, ev, state, config)
                if sig.get("signal") not in (None, "none", "hold"):
                    result.signals.append(sig)

    result.n_events = len(events)
    result.elapsed_real_s = time.monotonic() - t0
    result.elapsed_fake_s = (last_ts - first_ts) / 1000.0 if first_ts else 0.0
    result.final_basis = _basis_bps(book_okx, book_lt, config.unit_ratio)
    result.outcome = ReplayOutcome.COMPLETE
    return result


def run_sliced_replay(
    events: list[Event],
    config_a: ReplayConfig,
    config_b: ReplayConfig,
) -> SlicedReplayResult:
    """Run the same event feed through two configs for A/B comparison.

    Slice A is the baseline; slice B uses different params (e.g. n_slices > 1,
    tighter bands).  Order: A then B (both read-only on events, so order does not
    affect results).
    """
    symbol = config_a.symbol
    result_a = run_replay(events, config_a)
    result_b = run_replay(events, config_b)

    comparison = {
        "a_signals": len(result_a.signals),
        "b_signals": len(result_b.signals),
        "delta_signals": len(result_b.signals) - len(result_a.signals),
        "a_final_basis": round(result_a.final_basis, 2),
        "b_final_basis": round(result_b.final_basis, 2),
    }
    return SlicedReplayResult(symbol=symbol, slice_a=result_a, slice_b=result_b, comparison=comparison)