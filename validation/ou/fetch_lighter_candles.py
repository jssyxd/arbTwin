"""Fetch Lighter 1m candles (trade + mark price) into validation/ou/data/.

Endpoint discovered by probing (2026-11-10):
    GET https://mainnet.zklighter.elliot.ai/api/v1/candles
        ?market_id=<id>&resolution=1m&start_timestamp=<ms>&end_timestamp=<ms>&count_back=1500
    Same shape for /api/v1/markPriceCandles.
Quirks (all probed live, see findings.md):
    * `resolution` accepts 1m/5m/15m/30m/1h/4h/1d — NOT 1/60/1min/1s (code 20001).
    * `start_timestamp=0` is rejected; any nonzero start works.
    * `count_back` is honoured only when BOTH start_timestamp and end_timestamp
      are present; per-request page = 500–1500 rows depending on window.
    * Backward paging: set end_timestamp = (oldest_ts - 1), start_timestamp = 1.
    * /api/v1/markets returns 403 (bot wall); market_id mapping comes from
      /api/v1/orderBookDetails instead (saved as markets_orderBookDetails.json).

Usage:
    python validation/ou/fetch_lighter_candles.py --kind trade --days 90
    python validation/ou/fetch_lighter_candles.py --kind mark --days 90 \
        --symbols SOXL,COIN
Writes validation/ou/data/{SYM}_1m.json and {SYM}_mark_1m.json
(raw merged responses: metadata + deduped candle array).
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://mainnet.zklighter.elliot.ai/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

SYMBOLS = {  # from markets_orderBookDetails.json, all perp/active
    "SOXL": 197, "COIN": 109, "ORCL": 165, "SNDK": 139, "LIT": 120,
    "MSTR": 122, "MRVL": 174, "VVV": 69, "XPT": 147, "GRAM": 12,
    "EWY": 166, "TRUMP": 15, "BTC": 1, "ETH": 0,
}

UA = {"User-Agent": "ou-calib/1.0 (arbTwin validation T1)"}


def _get(url: str, retries: int = 6) -> dict:
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                # no Retry-After header from this server; back off hard
                time.sleep(max(30.0, 30 * (2 ** i)))
                continue
            raise
        except Exception:  # noqa: BLE001
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError("unreachable")


def fetch_symbol(sym: str, mid: int, kind: str, days: int) -> dict:
    ep = "candles" if kind == "trade" else "markPriceCandles"
    url0 = f"{BASE}/{ep}?market_id={mid}&resolution=1m&start_timestamp=1&end_timestamp=1999999999999&count_back=1500"
    d = _get(url0)
    rows = d["c"]
    if not rows:
        return {"symbol": sym, "n": 0}
    target = int((time.time() - days * 86400) * 1000)
    oldest = rows[0]["t"]
    reqs = 1
    while oldest > target and reqs < 400:
        u = f"{BASE}/{ep}?market_id={mid}&resolution=1m&start_timestamp=1&end_timestamp={oldest - 1}&count_back=1500"
        d2 = _get(u)
        r2 = d2["c"]
        reqs += 1
        if not r2 or r2[-1]["t"] >= oldest:
            break
        rows = r2 + rows
        oldest = r2[0]["t"]
        if reqs % 10 == 0:
            time.sleep(1.0)  # stay under the burst limit
    seen: dict[int, dict] = {}
    for row in rows:
        seen[row["t"]] = row
    candles = [seen[k] for k in sorted(seen)]
    out = {
        "symbol": sym, "market_id": mid, "resolution": "1m", "kind": kind,
        "fetched_at": int(time.time() * 1000), "requests": reqs,
        "n": len(candles), "oldest": candles[0]["t"], "newest": candles[-1]["t"],
        "span_days": (candles[-1]["t"] - candles[0]["t"]) / 86_400_000,
        "candles": candles,
    }
    name = f"{sym}_1m.json" if kind == "trade" else f"{sym}_mark_1m.json"
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["trade", "mark"], default="trade")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--symbols", default="all",
                    help="comma list or 'all' (12 approved + BTC/ETH)")
    ap.add_argument("--workers", type=int, default=2,
                    help="keep <=2: 4 parallel walks trigger HTTP 429")
    args = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)
    syms = (list(SYMBOLS) if args.symbols == "all"
            else [s.strip().upper() for s in args.symbols.split(",")])
    jobs = [(s, SYMBOLS[s], args.kind, args.days) for s in syms]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(lambda j: fetch_symbol(*j), jobs):
            print(f"{res['symbol']:5s} {args.kind:5s} n={res['n']:6d} "
                  f"span_d={res.get('span_days', 0):6.2f} reqs={res['requests']}",
                  flush=True)


if __name__ == "__main__":
    main()
