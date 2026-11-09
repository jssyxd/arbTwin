"""Shared Parquet/JSONL schemas for the validation harness.

Data conventions:
- All timestamps are UTC milliseconds (int64).
- `recv_ts` = when our collector received the datum (wall clock).
- `venue_ts` = venue-reported timestamp, which may be 0 when unavailable.
- `venue` is one of "okx" or "lighter".
- `symbol` is the canonical symbol (e.g. "AAPL", "1000SHIB").
"""

from __future__ import annotations

import pyarrow as pa
from dataclasses import dataclass, field
from typing import Optional

# ── Common column groups ───────────────────────────────────────────────────

_venue_symbol_ts = [
    ("venue", pa.utf8()),
    ("symbol", pa.utf8()),
    ("recv_ts", pa.int64()),
    ("venue_ts", pa.int64()),
]

# ── Quote / L2 snapshot ────────────────────────────────────────────────────

QUOTE_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("bid_px", pa.float64()),
        ("bid_sz", pa.float64()),
        ("ask_px", pa.float64()),
        ("ask_sz", pa.float64()),
    ]
)
QUOTE_COLUMNS = [f.name for f in QUOTE_SCHEMA]


L2_SNAPSHOT_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("bids", pa.list_(pa.struct([("px", pa.float64()), ("sz", pa.float64())]))),
        ("asks", pa.list_(pa.struct([("px", pa.float64()), ("sz", pa.float64())]))),
    ]
)
L2_SNAPSHOT_COLUMNS = [f.name for f in L2_SNAPSHOT_SCHEMA]


# ── Trades ──────────────────────────────────────────────────────────────────

TRADE_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("trade_id", pa.utf8()),
        ("px", pa.float64()),
        ("sz", pa.float64()),
        ("side", pa.utf8()),  # "buy" | "sell"
    ]
)
TRADE_COLUMNS = [f.name for f in TRADE_SCHEMA]


# ── Funding ─────────────────────────────────────────────────────────────────

FUNDING_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("funding_rate", pa.float64()),   # fraction per interval (0.0001 = 1 bp)
        ("funding_interval_h", pa.float64()),  # interval length in hours
        ("mark_price", pa.float64()),
        ("index_price", pa.float64()),
        ("next_funding_ts", pa.int64()),
    ]
)
FUNDING_COLUMNS = [f.name for f in FUNDING_SCHEMA]


# ── Orders / fills ──────────────────────────────────────────────────────────

ORDER_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("order_id", pa.utf8()),
        ("cl_ord_id", pa.utf8()),
        ("side", pa.utf8()),
        ("order_type", pa.utf8()),       # "limit" | "market"
        ("tif", pa.utf8()),              # "post_only" | "ioc" | "gtc" | "fok"
        ("qty", pa.float64()),
        ("px", pa.float64()),
        ("reduce_only", pa.bool_()),
        ("status", pa.utf8()),           # "new" | "partial" | "filled" | "cancelled" | "rejected"
        ("filled_qty", pa.float64()),
        ("avg_fill_px", pa.float64()),
        ("state_ts", pa.int64()),        # when this status snapshot was taken
    ]
)
ORDER_COLUMNS = [f.name for f in ORDER_SCHEMA]


FILL_SCHEMA = pa.schema(
    _venue_symbol_ts
    + [
        ("fill_id", pa.utf8()),
        ("order_id", pa.utf8()),
        ("cl_ord_id", pa.utf8()),
        ("side", pa.utf8()),
        ("px", pa.float64()),
        ("sz", pa.float64()),
        ("fee_bps", pa.float64()),
        ("role", pa.utf8()),            # "maker" | "taker"
    ]
)
FILL_COLUMNS = [f.name for f in FILL_SCHEMA]

# ── Manifest ────────────────────────────────────────────────────────────────


@dataclass
class DataManifest:
    """Describes what data is available for a symbol across venues.

    Each field is an optional path (relative to data root) or None if absent.
    The validator uses this to decide what tests can run.
    """

    symbol: str
    # path to Parquet or JSONL files; None if unavailable
    quotes_okx: Optional[str] = None
    quotes_lighter: Optional[str] = None
    l2_okx: Optional[str] = None
    l2_lighter: Optional[str] = None
    trades_okx: Optional[str] = None
    trades_lighter: Optional[str] = None
    funding_okx: Optional[str] = None
    funding_lighter: Optional[str] = None
    orders_okx: Optional[str] = None
    orders_lighter: Optional[str] = None
    fills_okx: Optional[str] = None
    fills_lighter: Optional[str] = None
    # conventions
    unit_ratio: float = 1.0        # how many Lighter base units = 1 OKX base unit
    metadata: dict = field(default_factory=dict)

    def both_l2(self) -> bool:
        return self.l2_okx is not None and self.l2_lighter is not None

    def both_trades(self) -> bool:
        return self.trades_okx is not None and self.trades_lighter is not None

    def both_quotes(self) -> bool:
        return self.quotes_okx is not None and self.quotes_lighter is not None

    def okx_data_present(self) -> bool:
        """Is any OKX data available at all?"""
        return any(
            v is not None
            for v in [
                self.quotes_okx,
                self.l2_okx,
                self.trades_okx,
                self.funding_okx,
            ]
        )

    def lighter_data_present(self) -> bool:
        return any(
            v is not None
            for v in [
                self.quotes_lighter,
                self.l2_lighter,
                self.trades_lighter,
                self.funding_lighter,
            ]
        )

    def mode(self) -> str:
        """Return the highest-fidelity mode that can run: l2, trade, quote, or none."""
        if self.both_l2():
            return "l2"
        if self.both_trades():
            return "trade"
        if self.both_quotes():
            return "quote"
        return "none"

    def enabled_metrics(self) -> list[str]:
        """Which metrics are computable given available data."""
        m = []
        if self.both_l2() or self.both_trades():
            m.extend(["p_fill", "markout", "p_fail", "capture_bps"])
        if self.both_l2():
            m.extend(["p_fill_by_level", "time_to_pair", "abort_cost_bps", "escalation_rate"])
        return m


def manifest_from_dict(d: dict) -> DataManifest:
    return DataManifest(**{k: v for k, v in d.items() if k in DataManifest.__dataclass_fields__})


ALL_SCHEMAS: dict[str, pa.Schema] = {
    "quote": QUOTE_SCHEMA,
    "l2_snapshot": L2_SNAPSHOT_SCHEMA,
    "trade": TRADE_SCHEMA,
    "funding": FUNDING_SCHEMA,
    "order": ORDER_SCHEMA,
    "fill": FILL_SCHEMA,
}