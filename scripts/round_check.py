#!/usr/bin/env python3
"""Round data check for the RWA two-leg program.

Reads `state/snapshot.jsonl` (written by `arbtwin run`) and reports, per symbol:
coverage, gaps, venue spreads, basis distribution by session, and action counts.
Exit 1 when the tape is missing, stale, or has a gap above the threshold.

Usage: python scripts/round_check.py [state/snapshot.jsonl] [--max-gap-s 10]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def session(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.weekday() >= 5:
        return "weekend"
    minutes = dt.hour * 60 + dt.minute
    if 13 * 60 + 30 <= minutes < 14 * 60 + 30:
        return "pre"
    if 14 * 60 + 30 <= minutes < 21 * 60:
        return "rth"
    if 21 * 60 <= minutes < 24 * 60 + 60:
        return "after"
    return "closed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="state/snapshot.jsonl")
    ap.add_argument("--max-gap-s", type=float, default=10.0)
    ap.add_argument("--out", default="state/checks/round_check.json")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"MISSING {path}")
        return 1
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        print(f"EMPTY {path}")
        return 1

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    ts_all = [r.get("ts", 0) for r in rows]
    for rec in rows:
        for s in rec.get("symbols", []):
            by_symbol[s["symbol"]].append({**s, "ts": rec["ts"]})

    report = {
        "path": str(path),
        "records": len(rows),
        "span_s": max(ts_all) - min(ts_all),
        "stale_s": datetime.now(tz=timezone.utc).timestamp() - max(ts_all),
        "max_gap_s": args.max_gap_s,
        "symbols": {},
        "fails": [],
    }
    for sym, items in sorted(by_symbol.items()):
        items.sort(key=lambda x: x["ts"])
        gaps = [b["ts"] - a["ts"] for a, b in zip(items, items[1:])]
        max_gap = max(gaps) if gaps else 0.0
        basis = [x["basis_bps"] for x in items if x.get("basis_bps") is not None]
        by_sess: dict[str, list[float]] = defaultdict(list)
        for x in items:
            if x.get("basis_bps") is not None:
                by_sess[session(x["ts"])].append(x["basis_bps"])
        entry = {
            "n": len(items),
            "first_ts": items[0]["ts"],
            "last_ts": items[-1]["ts"],
            "max_gap_s": round(max_gap, 2),
            "basis_p50": round(pct(basis, 0.50), 2) if basis else None,
            "basis_p95": round(pct(basis, 0.95), 2) if basis else None,
            "okx_spread_p50": round(pct([x["okx_spread_bps"] for x in items], 0.50), 2),
            "lt_spread_p50": round(pct([x["lt_spread_bps"] for x in items], 0.50), 2),
            "sessions": {
                k: {"n": len(v), "p50": round(pct(v, 0.50), 2), "p95": round(pct(v, 0.95), 2)}
                for k, v in sorted(by_sess.items())
            },
            "actions": {},
        }
        for x in items:
            entry["actions"][x.get("action", "?")] = entry["actions"].get(x.get("action", "?"), 0) + 1
        report["symbols"][sym] = entry
        if max_gap > args.max_gap_s:
            report["fails"].append(f"{sym}: gap {max_gap:.1f}s > {args.max_gap_s}s")
        if entry["n"] < 10:
            report["fails"].append(f"{sym}: only {entry['n']} rows")

    if report["stale_s"] > 300:
        report["fails"].append(f"tape stale {report['stale_s']:.0f}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2)[:4000])
    print(f"report -> {out}")
    if report["fails"]:
        print("FAIL: " + "; ".join(report["fails"]))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())