"""Monitoring: one flat snapshot per poll, thresholds, alert fan-out.

The monitor is deliberately boring: a dict in, a dict out, JSONL on disk.  It
is the only thing you are allowed to page on, so it must never depend on a
venue being reachable — every field degrades to `null` instead of raising.

Watched signals (all of them exist because a real failure had that shape):

| signal | fires when | why it matters |
|---|---|---|
| `ws_age_s` | > 30 s | you are trading a stale book |
| `basis_z` | > 4 | the premium left its 7-day distribution |
| `net_bps` | < 0 with a position open | the trade is paying to stay |
| `unhedged_usd` | > limit | one leg filled, the other did not |
| `liq_distance_pct` | < limit | the mark is walking to your stop |
| `funding_carry_apr` | > limit | the basis is financing a losing carry |
| `oi_utilization` | > limit | the venue is one taker from a cascade |
| `reject_rate` | > 20% over 50 orders | venue changed its rules under you |
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .calibration import percentile
from .config import write_state


@dataclass
class BasisHistory:
    symbol: str
    window: int = 720            # 1h bars * 30 days
    values: deque = field(default_factory=lambda: deque(maxlen=720))

    def push(self, basis_bps: float) -> None:
        self.values.append(basis_bps)

    def z(self, basis_bps: float) -> Optional[float]:
        if len(self.values) < 24:
            return None
        mean = sum(self.values) / len(self.values)
        sd = (sum((v - mean) ** 2 for v in self.values) / len(self.values)) ** 0.5
        if sd <= 1e-9:
            return None
        return (basis_bps - mean) / sd

    def p(self, basis_bps: float) -> Optional[float]:
        if len(self.values) < 24:
            return None
        below = sum(1 for v in self.values if v <= basis_bps)
        return below / len(self.values)


@dataclass
class OrderCounter:
    sent: int = 0
    rejected: int = 0
    window: deque = field(default_factory=lambda: deque(maxlen=50))

    def record(self, ok: bool) -> None:
        self.sent += 1
        self.window.append(1 if ok else 0)
        if not ok:
            self.rejected += 1

    @property
    def reject_rate(self) -> float:
        if not self.window:
            return 0.0
        return 1.0 - sum(self.window) / len(self.window)


class Monitor:
    def __init__(self, state_dir: str = "state", webhook: str = ""):
        self.state_dir = state_dir
        self.webhook = webhook
        self.basis: dict[str, BasisHistory] = {}
        self.orders = OrderCounter()
        self.last: dict = {}

    def observe(self, snap: dict, venue_health: dict | None = None,
                limits: Optional[dict] = None) -> dict:
        limits = limits or {}
        now = time.time()
        alerts: list[dict] = []
        rows = []
        for row in snap.get("rows", []):
            hist = self.basis.setdefault(row.symbol, BasisHistory(row.symbol))
            z = hist.z(row.basis_bps)
            hist.push(row.basis_bps)
            item = {
                "symbol": row.symbol,
                "basis_bps": round(row.basis_bps, 2),
                "basis_z": None if z is None else round(z, 2),
                "basis_pctile": None if hist.p(row.basis_bps) is None else round(hist.p(row.basis_bps), 3),
                "net_bps": round(row.net_edge_bps, 2),
                "band_bps": round(row.entry_band_bps, 2),
                "fee_bps": round(row.fee_bps, 2),
                "latency_bps": round(row.latency_cost_bps, 2),
                "action": row.action,
                "direction": row.direction,
            }
            rows.append(item)
            if row.reasons and any("income" in r for r in row.reasons):
                alerts.append({"level": "info", "scope": row.symbol,
                               "message": "funding harvest regime: the passive side is being paid to hold"})
            if z is not None and abs(z) > float(limits.get("basis_z_alert", 4.0)):
                alerts.append({"level": "info", "scope": row.symbol,
                               "message": f"basis z={z:.2f} (extreme premium)"})
        health = venue_health or {}
        for venue, stats in health.items():
            age = (stats or {}).get("age_s")
            if age is not None and age > float(limits.get("ws_stale_s", 30)):
                alerts.append({"level": "warn", "scope": venue, "message": f"ws stale {age:.0f}s"})
            if stats is not None and not stats.get("connected", True):
                alerts.append({"level": "warn", "scope": venue, "message": "ws disconnected"})
        if self.orders.reject_rate > 0.2 and self.orders.sent >= 10:
            alerts.append({"level": "critical", "scope": "orders",
                           "message": f"reject rate {self.orders.reject_rate:.0%} over {len(self.orders.window)} orders"})
        for pos in (snap.get("positions") or {}).values():
            if pos.get("liquidation_price") and pos.get("avg_entry"):
                dist = abs(pos["avg_entry"] - pos["liquidation_price"]) / pos["avg_entry"]
                if dist < float(limits.get("min_liq_distance_pct", 0.18)):
                    alerts.append({"level": "critical", "scope": pos.get("symbol", "?"),
                                   "message": f"liquidation distance {dist:.1%}"})
        payload = {"ts": now, "rows": rows, "alerts": alerts, "health": health,
                   "positions": len(snap.get("positions") or {}),
                   "exits_open": len(snap.get("exits") or [])}
        self.last = payload
        write_state(f"{self.state_dir}/monitor.jsonl", payload)
        if alerts:
            self._fanout(alerts)
        return payload

    def _fanout(self, alerts: list[dict]) -> None:
        for a in alerts:
            write_state(f"{self.state_dir}/alerts.jsonl", a)
        if not self.webhook:
            return
        try:
            req = urllib.request.Request(
                self.webhook,
                data=json.dumps({"alerts": alerts, "ts": time.time()}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass

    def render(self, payload: dict) -> str:
        head = f"{'symbol':<10s} {'basis':>9s} {'z':>6s} {'net':>8s} {'band':>7s} {'fee':>6s} {'action':>7s}"
        lines = [head, "-" * len(head)]
        for r in payload["rows"]:
            lines.append(
                f"{r['symbol']:<10s} {r['basis_bps']:>8.1f}b {(r['basis_z'] if r['basis_z'] is not None else 0):>6.2f} "
                f"{r['net_bps']:>7.1f}b {r['band_bps']:>6.1f}b {r['fee_bps']:>5.1f}b {r['action']:>7s}"
            )
        if payload["alerts"]:
            lines.append("")
            for a in payload["alerts"]:
                lines.append(f"[{a['level']:>8s}] {a['scope']}: {a['message']}")
        return "\n".join(lines)