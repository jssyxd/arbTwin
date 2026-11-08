#!/usr/bin/env python3
"""Hourly trading report + deviation detection, delivered through Hermes.

Deterministic on purpose: the numbers must not depend on an LLM.  `hermes send`
does the delivery (bot-token platforms need no gateway), and the same text is
also appended to state/reports/hourly.log so it can be audited offline.

Deviation rules (each one used to be an unseen failure):
  * a completed round whose realised bps < 50% of the symbol's expectation
  * abort rate over the window above 30%
  * no round at all for 24h while the symbol's session was open
  * snapshot older than 30 min, service not active, any critical alert
  * IOC escalation rate above 20% of rounds
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import yaml

STATE = Path("state")
ROUNDS = STATE / "rounds.jsonl"
SNAPSHOT = STATE / "snapshot.jsonl"
ALERTS = STATE / "alerts.jsonl"
DEV = STATE / "deviations.jsonl"
REPORTS = STATE / "reports"


def tail_jsonl(path: Path, limit: int = 5000) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def load_expectations() -> dict[str, dict]:
    exp: dict[str, dict] = {}
    for name in ("config/symbols.starter.yaml", "config/symbols.yaml"):
        p = Path(name)
        if not p.exists():
            continue
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for e in d.get("approved", []):
            exp.setdefault(e["symbol"], e)
    return exp


def pytest_like_rounds(rows: list[dict], since: float) -> list[dict]:
    """A round record is written on every transition; collapse by (symbol, round_id)."""
    by_round: dict[tuple[str, int], dict] = {}
    for r in rows:
        ts = float(r.get("ts") or 0) or float(r.get("entry_ts") or 0)
        key = (r.get("symbol", "?"), int(r.get("round_id") or 0))
        cur = by_round.setdefault(key, {"symbol": key[0], "round_id": key[1],
                                        "first_ts": ts, "last_ts": ts, "events": []})
        cur["first_ts"] = min(cur["first_ts"], ts) if ts else cur["first_ts"]
        cur["last_ts"] = max(cur["last_ts"], ts)
        cur["events"].append(r)
        for k in ("state", "pnl_bps", "abort_cost_bps"):
            if r.get(k) is not None:
                cur[k] = r[k]
    return [r for r in by_round.values() if r["last_ts"] >= since]


def summarise(rows: list[dict], expected: dict[str, dict], hours: float) -> dict:
    since = time.time() - hours * 3600
    rounds = pytest_like_rounds(rows, since)
    completed = [r for r in rounds if str(r.get("state")) == "flat" and r.get("pnl_bps") is not None]
    aborted = [r for r in rounds if any((ev.get("state") or "").startswith("abort") for ev in r["events"])]
    escalated = [r for r in rounds if any("escalat" in json.dumps(ev) for ev in r["events"])]
    pnls = [float(r["pnl_bps"]) for r in completed if r.get("pnl_bps") is not None]
    per_symbol: dict[str, list[float]] = defaultdict(list)
    for r in completed:
        if r.get("pnl_bps") is not None:
            per_symbol[r["symbol"]].append(float(r["pnl_bps"]))
    return {"rounds": len(rounds), "completed": len(completed), "aborted": len(aborted),
            "escalated": len(escalated), "pnls": pnls,
            "per_symbol": {k: v for k, v in per_symbol.items()},
            "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else 0.0,
            "avg_bps": (sum(pnls) / len(pnls)) if pnls else 0.0}


def health() -> dict:
    out = {"service": "unknown", "snapshot_age_s": None, "critical": 0}
    try:
        r = subprocess.run(["systemctl", "is-active", "arbtwin"], capture_output=True, text=True, timeout=5)
        out["service"] = r.stdout.strip() or "unknown"
    except Exception:
        pass
    if SNAPSHOT.exists():
        out["snapshot_age_s"] = int(time.time() - SNAPSHOT.stat().st_mtime)
    alerts = tail_jsonl(ALERTS, 2000)
    out["critical"] = sum(1 for a in alerts if a.get("level") == "critical")
    return out


def deviations(summary: dict, expected: dict[str, dict], h: dict, window_hours: float) -> list[dict]:
    out: list[dict] = []
    now = time.time()
    if h["service"] != "active":
        out.append({"kind": "ops", "key": "service_down", "detail": f"service={h['service']}"})
    if h["snapshot_age_s"] is not None and h["snapshot_age_s"] > 1800:
        out.append({"kind": "ops", "key": "snapshot_stale", "detail": f"{h['snapshot_age_s']}s"})
    if h["critical"]:
        out.append({"kind": "risk", "key": "critical_alerts", "detail": str(h["critical"])})
    if summary["rounds"]:
        abort_rate = summary["aborted"] / max(1, summary["rounds"])
        if abort_rate > 0.30:
            out.append({"kind": "expectation", "key": "abort_rate",
                        "detail": f"{abort_rate:.0%} of {summary['rounds']} rounds"})
        esc_rate = summary["escalated"] / max(1, summary["rounds"])
        if esc_rate > 0.20:
            out.append({"kind": "expectation", "key": "ioc_escalation",
                        "detail": f"{esc_rate:.0%} needed an IOC exit"})
    for sym, pnls in summary["per_symbol"].items():
        exp = float((expected.get(sym) or {}).get("net_per_round_bps") or 0.0)
        if not pnls or exp <= 0:
            continue
        avg = sum(pnls) / len(pnls)
        if avg < 0.5 * exp:
            out.append({"kind": "expectation", "key": f"under_expectation:{sym}",
                        "detail": f"realised {avg:+.2f}bps vs expected {exp:+.2f}bps"})
    if not summary["rounds"] and window_hours >= 24:
        out.append({"kind": "expectation", "key": "no_rounds_24h",
                    "detail": "no round opened in the last 24h"})
    return out


def render(summary: dict, h: dict, devs: list[dict], window_hours: float,
           expected: dict[str, dict]) -> str:
    lines = [f"📊 arbTwin {time.strftime('%m-%d %H:%M UTC', time.gmtime())} · last {window_hours:.0f}h"]
    lines.append(f"service={h['service']} snapshot={h['snapshot_age_s']}s critical={h['critical']}")
    lines.append(f"rounds={summary['rounds']} done={summary['completed']} "
                 f"abort={summary['aborted']} ioc={summary['escalated']}")
    if summary["pnls"]:
        lines.append(f"realised avg {summary['avg_bps']:+.2f}bps · win {summary['win_rate']:.0%} · n={len(summary['pnls'])}")
        for sym, pnls in summary["per_symbol"].items():
            exp = float((expected.get(sym) or {}).get("net_per_round_bps") or 0.0)
            lines.append(f"  {sym}: {sum(pnls)/len(pnls):+.2f}bps/round (exp {exp:+.2f}, n={len(pnls)})")
    else:
        lines.append("no completed round in the window")
    if devs:
        lines.append("⚠️ deviations:")
        for d in devs[:6]:
            lines.append(f"  - {d['kind']}/{d['key']}: {d['detail']}")
    else:
        lines.append("no deviation vs expectation")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--send", action="store_true", help="deliver through hermes send")
    ap.add_argument("--target", default="telegram")
    args = ap.parse_args()

    expected = load_expectations()
    rows = tail_jsonl(ROUNDS)
    summary = summarise(rows, expected, args.hours)
    h = health()
    devs = deviations(summary, expected, h, args.hours)
    text = render(summary, h, devs, args.hours, expected)

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "hourly.log").open("a", encoding="utf-8").write(text + "\n\n")
    for d in devs:
        d.update({"ts": time.time(), "window_hours": args.hours})
        DEV.parent.mkdir(parents=True, exist_ok=True)
        with DEV.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(text)
    if args.send:
        body = REPORTS / "hourly.body.txt"
        body.write_text(text + "\n", encoding="utf-8")
        r = subprocess.run(["hermes", "send", "--to", args.target, "--file", str(body)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"hermes send failed rc={r.returncode}: {r.stderr[:300]}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
