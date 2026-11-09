# validation/harness — Offline validation harness for arbTwin

Deterministic replay, fill modelling, and metrics for basis-trade strategy
validation.  No network required for core tests; real exchange data loaded
from local Parquet/JSONL files.

## Quick start

```bash
# Run self-test (no network, synthetic data)
python -m validation.harness.selftest

# Run from a data manifest
python -c "
from validation.harness import DataManifest, run_replay, Event, EventKind, ReplayConfig
# ...
"
```

## Package layout

| Module | Purpose |
|--------|---------|
| `manifest.py` | Parquet/JSONL schemas for quotes, L2, trades, funding, orders, fills; `DataManifest` |
| `loaders.py` | Read local Parquet/JSONL; optional Lighter REST/WS fetcher (only when data absent) |
| `replay.py` | Deterministic fake-clock event loop; `n_slices=1` and sliced A/B; pluggable strategy callback |
| `fill_model.py` | `fill_model()` interface — conservative default (price-through only) + queue-reactive hook |
| `metrics.py` | p_fill, p_fill_by_level, time-to-pair, p_fail, markout, capture_bps, abort_cost_bps, escalation rate |
| `selftest.py` | Synthetic two-venue book/trade generator; `python -m` runner |

## Data schemas

All timestamps are UTC milliseconds (`int64`). Every record has:

- `venue`: `"okx"` or `"lighter"`
- `symbol`: canonical symbol (e.g. `"AAPL"`, `"1000SHIB"`)
- `recv_ts`: collector receipt time (wall clock)
- `venue_ts`: venue-reported timestamp (may be 0)

### Quote (top-of-book)
`bid_px, bid_sz, ask_px, ask_sz` — one snapshot per row.

### L2 snapshot
`bids: list<struct<px:float64, sz:float64>>`, `asks: list<struct<px:float64, sz:float64>>`.

### Trade
`trade_id, px, sz, side` ("buy"|"sell").

### Funding
`funding_rate` (fraction per interval), `funding_interval_h`, `mark_price`, `index_price`, `next_funding_ts`.

### Order
`order_id, cl_ord_id, side, order_type, tif, qty, px, reduce_only, status, filled_qty, avg_fill_px, state_ts`.

### Fill
`fill_id, order_id, cl_ord_id, side, px, sz, fee_bps, role` ("maker"|"taker").

## Fill models

```python
from validation.harness import conservative_fill, queue_reactive_fill, set_fill_model

# Conservative: only fills when price crosses resting level
qty = conservative_fill({"px": 99.0, "sz": 5.0}, {"side": "buy", "px": 100.0, "remaining": 10.0})
# qty = 10.0 (trade at 99 crosses our 100 bid)

# Queue-reactive: also fills at same level when queue ahead is cleared
qty = queue_reactive_fill({"px": 100.0, "sz": 3.0},
                           {"side": "buy", "px": 100.0, "remaining": 10.0},
                           queue_ahead=0.0)
# qty = 3.0 (we're at the front of the queue)

# Global override
set_fill_model(queue_reactive_fill)
```

## Replay

```python
from validation.harness import Event, EventKind, ReplayConfig, run_replay

config = ReplayConfig(symbol="AAPL", unit_ratio=1.0)

events = [
    Event(ts=1000, kind=EventKind.BOOK, venue="okx", symbol="AAPL",
          payload={"bids": [(99.0, 10.0)], "asks": [(101.0, 10.0)]}),
    Event(ts=1000, kind=EventKind.BOOK, venue="lighter", symbol="AAPL",
          payload={"bids": [(99.5, 10.0)], "asks": [(101.5, 10.0)]}),
    # ...
]
events.sort(key=lambda e: e.ts)

result = run_replay(events, config)
print(result.to_dict())
```

## Metrics

```python
from validation.harness import compute_p_fill, compute_markout, compute_all_metrics

# Individual metrics
p = compute_p_fill(fills_df, quotes_df, distance_bps=0.0, horizon_s=60.0)
m = compute_markout(fills_df, quotes_df)

# All at once
all_metrics = compute_all_metrics(fills_df, quotes_df, l2_data=l2_df)
for r in all_metrics:
    print(r.name, r.value, r.unit)
```

## Network dependency

- Core replay, metrics, and selftest: **no network required**.
- `loaders.fetch_lighter_quotes()`: uses Lighter mainnet/testnet WebSocket (optional fallback when local data absent).
- OKX: unreachable from this Windows host (DNS → 169.254.0.2). OKX data must be pre-staged from a VPS.

## Tested environment

- Python 3.12.9, pandas 2.3.3, pyarrow 25.0.1, numpy 2.5.1
- Windows 10, W: drive (SMB-backed, 84 GB free)