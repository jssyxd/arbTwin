#!/usr/bin/env python3
"""RWA live status snapshot: service, tape, positions, PnL, alerts."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from arbtwin.config import AppConfig
from arbtwin.engine import TwinEngine

BASE_EQUITY = 59.108  # 2026-11-10 before R1 live


def tail_jsonl(path: Path, n: int = 1) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main() -> int:
    cfg = AppConfig.load()
    cfg.dry_run = False
    eng = TwinEngine(cfg, proxy=os.environ.get("ARBTWIN_PROXY"))
    try:
        eq = {"okx": eng.okx.equity_usd(), "lighter": eng.lighter.equity_usd()}
    except Exception as exc:
        eq = {"error": str(exc)[:160]}
    try:
        pos = {k: round(v.qty_base, 8) for k, v in eng.broker.positions().items()}
    except Exception as exc:
        pos = {"error": str(exc)[:160]}
    state = Path(cfg.state_dir)
    snap = tail_jsonl(state / "snapshot.jsonl", 1)
    trades = tail_jsonl(state / "trades.jsonl", 5)
    alerts = tail_jsonl(state / "alerts.jsonl", 50)
    now = time.time()
    critical = [a for a in alerts if a.get("level") == "critical"
                and now - float(a.get("ts", 0)) < 3600]
    total = sum(v for v in eq.values() if isinstance(v, (int, float)))
    payload = {
        "ts": now,
        "equity": eq,
        "total_equity": round(total, 4) if total else None,
        "pnl_vs_base": round(total - BASE_EQUITY, 4) if total else None,
        "positions": pos,
        "last_snapshot": snap[0] if snap else None,
        "recent_trades": trades,
        "critical_last_h": len(critical),
    }
    out = state / "status.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    with (state / "status.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
    print(json.dumps(payload, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())