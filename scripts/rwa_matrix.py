#!/usr/bin/env python3
"""Build the Lighter <-> OKX RWA/equity matrix: mapping, live price/spread, 14d basis.

Usage: python scripts/rwa_matrix.py --out state/rwa_matrix.json
Network: public endpoints only; 6 workers, throttled for OKX's 20 req / 2s.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import statistics as st
import time
import urllib.request
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PROXY = "http://192.168.1.5:7890"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY}))
OP.addheaders = [("User-Agent", "arbtwin/rwa-matrix")]

ALIAS = {"SAMSUNG": "SAMSUNGUSD", "SKHYNIX": "SKHYNIXUSD", "HYUNDAI": "HYUNDAIUSD"}
REQUESTED = """US500 US100 XAU XAG EURUSD TUR30 WHEAT SPACEX BABA TSM CRWV SAMSUNG SKHYNIX MRVL AAOI
MSTR IREN KORU KIOXIA RKLB BMNR CRCL CBRS NVDA TSLA AAPL GPRO ASML ON ALAB GEV NOW IBM MCD JPM V
GS MURATA OXY SMR ADI FLEX MELI PEP UMC WUXIBIO GIGADEV PDD BIDU GILD PFE CEG BAC IBKR MPWR
SPY QQQ XLV EIDO EWZS ANTHROPIC WTI GOLD""".split()


def get(url: str, tries: int = 3, timeout: int = 20):
    last = None
    for _ in range(tries):
        try:
            return json.load(OP.open(url, timeout=timeout))
        except Exception as exc:
            last = exc
            time.sleep(0.4)
    raise last


def session_of(ts_ms: int) -> str:
    t = dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc).astimezone(NY)
    if t.weekday() >= 5:
        return "weekend"
    m = t.hour * 60 + t.minute
    if 4 * 60 <= m < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= m < 16 * 60:
        return "rth"
    if 16 * 60 <= m < 20 * 60:
        return "afterhours"
    return "overnight"


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = q * (len(xs) - 1)
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def book_spread(venue: str, ref: str) -> tuple[float, float, float]:
    if venue == "okx":
        d = get(f"https://www.okx.com/api/v5/market/books?instId={ref}&sz=5")
        b = d["data"][0]
        bid, ask = float(b["bids"][0][0]), float(b["asks"][0][0])
    else:
        d = get(f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={ref}&limit=5")
        bid, ask = float(d["bids"][0]["price"]), float(d["asks"][0]["price"])
    mid = (bid + ask) / 2
    return mid, (ask - bid) / mid * 1e4, mid


def okx_series(inst: str, days: int = 14) -> dict[int, float]:
    out: dict[int, float] = {}
    d = get(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar=1H&limit=300")
    for r in d.get("data", []):
        out[int(r[0])] = float(r[4])
    oldest = min(out) if out else int(time.time() * 1000)
    target = int((time.time() - days * 86400) * 1000)
    while oldest > target:
        p = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&after={oldest}&limit=100")
        rows = p.get("data", [])
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = float(r[4])
        nxt = min(int(r[0]) for r in rows)
        if nxt >= oldest:
            break
        oldest = nxt
    return out


def lighter_series(mid: str, days: int = 14) -> dict[int, float]:
    now = int(time.time() * 1000)
    start = int((time.time() - days * 86400) * 1000)
    d = get(f"https://mainnet.zklighter.elliot.ai/api/v1/markPriceCandles?market_id={mid}"
            f"&resolution=1h&start_timestamp={start}&end_timestamp={now}&count_back=500")
    return {int(r["t"]): float(r["c"]) for r in d.get("c", d.get("candles", []))}


def analyse(sym: str, okx_id: str, lt_ref: str) -> dict:
    row = {"sym": sym, "okx": okx_id, "lighter_id": lt_ref}
    try:
        okx_mid, okx_spr, _ = book_spread("okx", okx_id)
        lt_mid, lt_spr, _ = book_spread("lighter", lt_ref)
        row.update({"okx_px": okx_mid, "okx_spread_bps": okx_spr,
                    "lt_px": lt_mid, "lt_spread_bps": lt_spr,
                    "ratio": lt_mid / okx_mid if okx_mid else None})
    except Exception as exc:
        row["book_err"] = str(exc)[:80]
        return row
    try:
        a, b = okx_series(okx_id), lighter_series(lt_ref)
        keys = sorted(set(a) & set(b))
        if len(keys) >= 48:
            ratio = st.fmean([b[k] / a[k] for k in keys])
            basis = [(b[k] / ratio - a[k]) / a[k] * 1e4 for k in keys]
            row.update({"n": len(keys), "hist_ratio": ratio,
                        "px14d_min": min(a[k] for k in keys), "px14d_max": max(a[k] for k in keys),
                        "basis_p50": st.median(basis),
                        "basis_p95_abs": percentile([abs(x) for x in basis], 0.95),
                        "ratio_cv_pct": st.pstdev([b[k] / a[k] for k in keys]) / ratio * 100})
            for name in ("rth", "premarket", "afterhours", "overnight", "weekend"):
                sub = [abs(base) for k, base in zip(keys, basis) if session_of(k) == name]
                row[f"basis_p95_{name}"] = percentile(sub, 0.95) if len(sub) >= 10 else None
                row[f"n_{name}"] = len(sub)
    except Exception as exc:
        row["hist_err"] = str(exc)[:80]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="state/rwa_matrix.json")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    swap = {i["instId"].split("-")[0]: i["instId"]
            for i in get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")["data"]
            if i["instId"].endswith("-USDT-SWAP")}
    spot = {i["instId"] for i in get("https://www.okx.com/api/v5/public/instruments?instType=SPOT")["data"]}
    d = get("https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails")
    lt_perp = {o["symbol"]: o for o in d["order_book_details"] if o.get("market_type") == "perp"}
    lt_spot = {o["symbol"]: o for o in (d.get("spot_order_book_details") or [])}

    pairs = []
    for sym in REQUESTED:
        lt_sym = ALIAS.get(sym, sym)
        lt = lt_perp.get(lt_sym)
        lt_ref = lt["market_id"] if lt else None
        if lt_ref is None:
            for cand in (f"{sym}/USDC", f"rh{sym}/USDC", f"{lt_sym}/USDC"):
                if cand in lt_spot:
                    lt_ref = lt_spot[cand]["market_id"]
                    break
        okx_id = swap.get(sym) or (f"X{sym}-USDT" if f"X{sym}-USDT" in spot else None)
        if okx_id is None and sym in ("SPY", "QQQ"):
            okx_id = f"X{sym}-USDT" if f"X{sym}-USDT" in spot else None
        pairs.append({"sym": sym, "okx": okx_id, "lighter_id": lt_ref,
                      "okx_quote": "swap" if okx_id in swap.values() else ("spot" if okx_id else None)})

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(lambda p: analyse(p["sym"], p["okx"], str(p["lighter_id"]))
                           if p["okx"] and p["lighter_id"] else p, pairs))
    for r, p in zip(rows, pairs):
        r.setdefault("okx_quote", p["okx_quote"])
        r.setdefault("okx", p["okx"])
        r.setdefault("lighter_id", p["lighter_id"])
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "days": args.days, "rows": rows}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    both = [r for r in rows if r.get("okx") and r.get("lighter_id")]
    print(f"rows {len(rows)} | on both venues {len(both)} | with history {sum(1 for r in both if r.get('basis_p95_abs'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
