"""Historical data helpers shared by screening and replay.

Both venues expose hourly closes without auth; that is enough to calibrate a
unit ratio, measure volatility and build a basis distribution.  Nothing here
is allowed to look forward: the loader hands back plain `{ts_ms: close}` maps
and the consumers align them by timestamp.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from .models import LIGHTER, OKX
from .venues.base import http_json
from .venues.lighter import LighterVenue
from .venues.okx import OkxVenue

CACHE_DIR = os.environ.get("ARBTWIN_CACHE", "state/cache")


def _cache_path(name: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


def okx_hourly(venue: OkxVenue, symbol: str, days: int = 7) -> dict[int, float]:
    spec = venue.specs([symbol]).get(symbol)
    if not spec:
        return {}
    out: dict[int, float] = {}
    inst = spec.market_ref
    data = http_json(f"{venue.creds.base_url}/api/v5/market/candles",
                     params={"instId": inst, "bar": "1H", "limit": 300}, proxy=venue.proxy)
    for row in data.get("data", []):
        out[int(row[0])] = float(row[4])
    oldest = min(out) if out else int(time.time() * 1000)
    target = int((time.time() - days * 86400) * 1000)
    while oldest > target:
        page = http_json(f"{venue.creds.base_url}/api/v5/market/history-candles",
                         params={"instId": inst, "bar": "1H", "after": oldest, "limit": 100},
                         proxy=venue.proxy)
        rows = page.get("data", [])
        if not rows:
            break
        for row in rows:
            out[int(row[0])] = float(row[4])
        new_oldest = min(int(r[0]) for r in rows)
        if new_oldest >= oldest:
            break
        oldest = new_oldest
    return out


def lighter_hourly(venue: LighterVenue, symbol: str, days: int = 7) -> dict[int, float]:
    spec = venue.specs([symbol]).get(symbol)
    if not spec:
        return {}
    end = int(time.time() * 1000)
    start = int((time.time() - days * 86400) * 1000)
    data = http_json(f"{venue.creds.base_url}/api/v1/markPriceCandles",
                     params={"market_id": spec.market_ref, "resolution": "1h",
                             "start_timestamp": start, "end_timestamp": end, "count_back": 1000},
                     proxy=venue.proxy)
    out: dict[int, float] = {}
    for row in data.get("c", data.get("candles", [])):
        out[int(row["t"])] = float(row["c"])
    return out


def both_hourly(okx: OkxVenue, lighter: LighterVenue, symbol: str, days: int = 7,
                use_cache: bool = True) -> tuple[dict[int, float], dict[int, float]]:
    key = f"{symbol}_{days}d.json"
    path = _cache_path(key)
    if use_cache and os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 1800:
        payload = json.load(open(path, encoding="utf-8"))
        return ({int(k): v for k, v in payload["okx"].items()},
                {int(k): v for k, v in payload["lighter"].items()})
    a = okx_hourly(okx, symbol, days)
    b = lighter_hourly(lighter, symbol, days)
    if use_cache and a and b:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"okx": a, "lighter": b, "ts": time.time()}, fh)
    return a, b


def candidate_symbols(okx: OkxVenue, lighter: LighterVenue) -> list[tuple[str, str, str]]:
    """(symbol, okx_inst, lighter_market_id) for instruments listed on both.

    Lighter re-denominates some majors (1000SHIB, 1000PEPE ...).  We surface
    those as candidates and let `calibration` prove the multiplier instead of
    hardcoding an alias table.
    """
    okx_specs = okx.specs()
    lt_specs = lighter.specs()
    out: list[tuple[str, str, str]] = []
    for symbol, spec in lt_specs.items():
        if symbol in okx_specs:
            out.append((symbol, okx_specs[symbol].market_ref, spec.market_ref))
            continue
        stripped = symbol
        for prefix in ("1000", "1M"):
            if symbol.startswith(prefix):
                stripped = symbol[len(prefix):]
                break
        if stripped in okx_specs:
            out.append((symbol, okx_specs[stripped].market_ref, spec.market_ref))
    return out