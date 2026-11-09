"""Data loaders for the validation harness.

Reads local Parquet/JSONL files; optional Lighter REST/WS fetcher used only
when data is absent from disk.  OKX is unreachable from this Windows host
(DNS resolves api.okx.com to 169.254.0.2), so OKX fetching is not attempted
— OKX data must be pre-staged or supplied from a VPS.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .manifest import (
    DataManifest,
    FILL_COLUMNS,
    FUNDING_COLUMNS,
    L2_SNAPSHOT_COLUMNS,
    ORDER_COLUMNS,
    QUOTE_COLUMNS,
    TRADE_COLUMNS,
    ALL_SCHEMAS,
)

log = logging.getLogger("validation.harness.loaders")

# Default data root — can be overridden
DATA_ROOT: Path = Path(__file__).resolve().parent.parent / "data"


def _read_table(path: Path, schema_name: str) -> pd.DataFrame:
    """Read a Parquet or JSONL file, validating against schema."""
    if not path.exists():
        raise FileNotFoundError(f"data file not found: {path}")

    schema = ALL_SCHEMAS.get(schema_name)
    if path.suffix in (".parquet", ".pq"):
        table = pq.read_table(path)
        if schema:
            table = table.cast(schema)
    elif path.suffix in (".jsonl", ".json"):
        table = pq.read_json(path)
        if schema:
            table = table.cast(schema)
    else:
        raise ValueError(f"unsupported format: {path.suffix} (expected .parquet or .jsonl)")

    return table.to_pandas()


# ── Typed loaders ───────────────────────────────────────────────────────────


def load_quotes(path: str | Path) -> pd.DataFrame:
    """Load top-of-book quotes snapshot file.

    Returns DataFrame with columns: venue, symbol, recv_ts, venue_ts,
    bid_px, bid_sz, ask_px, ask_sz.
    """
    df = _read_table(Path(path), "quote")
    return df[df.columns.intersection(QUOTE_COLUMNS)]


def load_l2(path: str | Path) -> pd.DataFrame:
    """Load full L2 snapshot file.

    Returns DataFrame with columns: venue, symbol, recv_ts, venue_ts,
    bids (list of struct{px,sz}), asks (list of struct{px,sz}).
    """
    df = _read_table(Path(path), "l2_snapshot")
    return df[df.columns.intersection(L2_SNAPSHOT_COLUMNS)]


def load_trades(path: str | Path) -> pd.DataFrame:
    """Load tick-level trade file.

    Returns DataFrame with columns: venue, symbol, recv_ts, venue_ts,
    trade_id, px, sz, side.
    """
    df = _read_table(Path(path), "trade")
    df = df[df.columns.intersection(TRADE_COLUMNS)]
    # ensure sorted by recv_ts
    if "recv_ts" in df.columns:
        df = df.sort_values("recv_ts").reset_index(drop=True)
    return df


def load_funding(path: str | Path) -> pd.DataFrame:
    """Load funding-rate history file.

    Returns DataFrame with: venue, symbol, recv_ts, venue_ts,
    funding_rate, funding_interval_h, mark_price, index_price, next_funding_ts.
    """
    df = _read_table(Path(path), "funding")
    return df[df.columns.intersection(FUNDING_COLUMNS)]


def load_orders(path: str | Path) -> pd.DataFrame:
    """Load order-lifecycle event file."""
    df = _read_table(Path(path), "order")
    return df[df.columns.intersection(ORDER_COLUMNS)]


def load_fills(path: str | Path) -> pd.DataFrame:
    """Load fill event file."""
    df = _read_table(Path(path), "fill")
    return df[df.columns.intersection(FILL_COLUMNS)]


# ── Loading from manifest ──────────────────────────────────────────────────


def load_manifest_data(manifest: DataManifest, data_root: str | Path | None = None) -> dict[str, pd.DataFrame | None]:
    """Load all available data referenced by a manifest.

    Returns a dict keyed by slot name (e.g. 'l2_okx', 'trades_lighter') with
    DataFrames (or None when path is absent or loading fails).
    """
    root = Path(data_root) if data_root else DATA_ROOT
    slots = [
        ("quotes_okx", load_quotes),
        ("quotes_lighter", load_quotes),
        ("l2_okx", load_l2),
        ("l2_lighter", load_l2),
        ("trades_okx", load_trades),
        ("trades_lighter", load_trades),
        ("funding_okx", load_funding),
        ("funding_lighter", load_funding),
        ("orders_okx", load_orders),
        ("orders_lighter", load_orders),
        ("fills_okx", load_fills),
        ("fills_lighter", load_fills),
    ]
    result: dict[str, pd.DataFrame | None] = {}
    for attr, loader in slots:
        rel = getattr(manifest, attr, None)
        if rel is None:
            result[attr] = None
            continue
        path = root / rel
        if not path.exists():
            log.warning("manifest references missing file: %s", path)
            result[attr] = None
            continue
        try:
            result[attr] = loader(path)
        except Exception as exc:
            log.error("failed to load %s: %s", attr, exc)
            result[attr] = None
    return result


# ── Lighter REST/WS fetcher (optional, no network required for offline use) ─


def fetch_lighter_quotes(
    symbol: str,
    market_id: str,
    data_root: str | Path | None = None,
    duration_s: float = 30.0,
    testnet: bool = False,
) -> Optional[pd.DataFrame]:
    """Fetch top-of-book quotes from Lighter WebSocket, return as DataFrame.

    Only used when local data is absent.  Requires network access to Lighter.
    """
    try:
        import asyncio
        import websockets
    except ImportError:
        log.warning("websockets not installed; cannot fetch Lighter data live")
        return None

    base = "testnet.zklighter.elliot.ai" if testnet else "mainnet.zklighter.elliot.ai"
    url = f"wss://{base}"

    async def _collect():
        rows = []
        async with websockets.connect(url) as ws:  # type: ignore[no-untyped-call]
            sub = json.dumps(
                {
                    "method": "subscribe",
                    "params": {"channel": "order_book", "markets": [market_id]},
                }
            )
            await ws.send(sub)
            deadline = time.time() + duration_s
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.time()))
                    msg = json.loads(raw)
                    data = msg.get("data", {})
                    rows.append(
                        {
                            "venue": "lighter",
                            "symbol": symbol,
                            "recv_ts": int(time.time() * 1000),
                            "venue_ts": int(data.get("timestamp", 0) * 1000),
                            "bid_px": float(data.get("best_bid", 0) or 0),
                            "bid_sz": float(data.get("best_bid_size", 0) or 0),
                            "ask_px": float(data.get("best_ask", 0) or 0),
                            "ask_sz": float(data.get("best_ask_size", 0) or 0),
                        }
                    )
                except asyncio.TimeoutError:
                    break
                except Exception:
                    break
        return rows

    try:
        rows = asyncio.run(_collect())
    except Exception as exc:
        log.warning("Lighter WS fetch failed: %s", exc)
        return None

    if not rows:
        return None

    df = pd.DataFrame(rows)
    root = Path(data_root) if data_root else DATA_ROOT
    out_path = root / "lighter" / symbol / "quotes.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("saved %d Lighter quotes to %s", len(df), out_path)
    return df