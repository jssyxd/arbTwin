"""Position-delta reconciliation: the only honest source of "did we fill?".

Book-crossing is a dry-run proxy.  Live, the venue is the authority: we poll the
account's positions and turn deltas into fills.  That is deliberately coarser than
an order-state feed (no queue, no partial-fill detail) but it cannot drift from
reality: every unit reported here exists on the venue.

Rules:
* the ledger persists to disk, so a restart does not replay old deltas as new fills;
* a fill's side comes from the sign of the delta, its price from the venue's
  `avg_entry_price` when the position grew (the venue already averaged it);
* deltas below `eps` are ignored (rounding dust from contract sizes).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class FillEvent:
    venue: str
    symbol: str
    side: str              # buy | sell
    qty_base: float        # always positive
    px: float
    qty_after: float


@dataclass
class PositionLedger:
    path: str = "state/positions.json"
    eps: float = 1e-9
    _state: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.load()

    # ------------------------------------------------------------------ storage
    def load(self) -> None:
        p = Path(self.path)
        if p.exists():
            try:
                self._state = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._state = {}
        self._state.setdefault("positions", {})

    def save(self) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # ------------------------------------------------------------------ logic
    def observe(self, venue: str, symbol: str, qty: float, avg_entry: float = 0.0,
                mark: float = 0.0) -> Optional[FillEvent]:
        key = f"{venue}:{symbol}"
        prev = float(self._state["positions"].get(key, {}).get("qty", 0.0))
        delta = qty - prev
        self._state["positions"][key] = {"qty": qty, "avg_entry": avg_entry, "mark": mark}
        if abs(delta) <= max(self.eps, 1e-12):
            return None
        px = avg_entry if avg_entry > 0 else mark
        if px <= 0:
            px = 0.0
        return FillEvent(venue=venue, symbol=symbol,
                         side="buy" if delta > 0 else "sell",
                         qty_base=abs(delta), px=px, qty_after=qty)

    def seed(self, positions: dict) -> None:
        """Adopt current venue positions WITHOUT emitting fills.

        Called once at startup: the ledger may hold yesterday's quantities (or a
        different account's), and treating "ledger says 30, venue says 30" as no
        event is right, while treating a stale mismatch as a fill is not.
        """
        for key, pos in positions.items():
            venue, _, symbol = key.partition(":")
            self._state["positions"][f"{venue or getattr(pos, 'venue', '')}:"
                                     f"{symbol or getattr(pos, 'symbol', '')}"] = {
                "qty": float(getattr(pos, "qty_base", 0.0)),
                "avg_entry": float(getattr(pos, "avg_entry", 0.0) or 0.0),
                "mark": float(getattr(pos, "avg_entry", 0.0) or 0.0),
            }
        self.save()

    def reconcile(self, positions: dict) -> list[FillEvent]:
        """`positions` is {f"{venue}:{symbol}": VenuePosition} from the broker."""
        events: list[FillEvent] = []
        for key, pos in positions.items():
            venue, _, symbol = key.partition(":")
            ev = self.observe(venue or getattr(pos, "venue", ""),
                              symbol or getattr(pos, "symbol", ""),
                              float(getattr(pos, "qty_base", 0.0)),
                              float(getattr(pos, "avg_entry", 0.0) or 0.0),
                              float(getattr(pos, "avg_entry", 0.0) or 0.0))
            if ev is not None:
                events.append(ev)
        # positions that vanished (closed at the venue) also matter
        seen = {f"{p.venue}:{p.symbol}" for p in events}
        for key, row in list(self._state["positions"].items()):
            if key in seen:
                continue
            prev = float(row.get("qty", 0.0))
            if abs(prev) <= self.eps:
                continue
            venue, _, symbol = key.partition(":")
            self._state["positions"][key] = {"qty": 0.0, "avg_entry": row.get("avg_entry", 0.0), "mark": row.get("mark", 0.0)}
            events.append(FillEvent(venue=venue, symbol=symbol,
                                    side="sell" if prev > 0 else "buy",
                                    qty_base=abs(prev), px=float(row.get("mark") or row.get("avg_entry") or 0.0),
                                    qty_after=0.0))
        if events:
            self.save()
        return events

    def snapshot(self) -> dict:
        return dict(self._state["positions"])
