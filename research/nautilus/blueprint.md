# NautilusTrader Integration Blueprint: OKX ↔ Lighter Basis Engine

## 1. Version & Platform Facts

| Fact | Value | Source |
|---|---|---|
| NT stable release | **2.0.0rc8** (pre-release, 2026-10-28) | `github.com/nautechsystems/nautilus_trader/releases/tag/v2.0.0rc8` |
| Previous stable tag | 1.231.0 Beta (before 2.0 RC series) | [INFERENCE] from release list |
| Python support | ≥3.12, Linux x86_64/ARM64, macOS ARM64 | `nautilustrader.io/docs/latest/getting_started/installation` |
| Rust MSRV | Latest stable; heavy Rust compiler improvements required | README.md |
| PyO3 bindings | All adapters exposed to Python via PyO3 | Each adapter doc |
| Key 2.0 changes | Cython removed; PyO3-native FFI; `embedded_*` strategies; gzip HTTP; batched subs/historical; `Money` exact precision | v2.0.0rc8 release notes |

### Recent execution/risk engine changes (v2.0.0rc5–rc8)

- **Lighter**: scheduled cancellation with auto-renewal; self-trade behavior; `liquidation_price` in position reports; proactive WS reconnect on `shutdown` notices; account-wide cancel-all. Native OTO/OCO/OTOCO grouped orders via `CreateGroupedOrders`.
- **OKX**: startup reconciliation history page limits; account bills/archive queries; RPI (Retail Price Improvement) maker/taker access; `instIdCode`-based order routing.
- **Core**: `OmsType.HEDGING` / `NETTING` virtual positions; `RiskEngine` trading-state enforcement (`ACTIVE`/`REDUCING`/`HALTED`); `full_position_exit_venues` for whole-position conditional exits; `OrderEmulator` → `RiskEngine` path for released emulated orders; `ExecutionEngineConfig.carry_replay_events_on_reopen`.

## 2. Multi-Venue Support

### Adapter Status

| Venue | NT Adapter | Status | Type |
|---|---|---|---|
| **OKX** | `nautilus-okx` (Rust, PyO3) | **Official**, in-tree | Data + Execution |
| **Lighter** | `nautilus-lighter` (Rust, PyO3) | **Official**, in-tree | Data + Execution |
| **Hyperliquid** | `nautilus-hyperliquid` | Official, in-tree | Data + Execution |
| **Bybit** | `nautilus-bybit` | Official, in-tree | Data + Execution |
| **Binance** | `nautilus-binance` | Official, in-tree | Both spot + futures |
| **dYdX** | `nautilus-dydx` | Official, in-tree | Data + Execution |

**Key finding**: Both OKX and Lighter have production-grade, Rust-native, PyO3-exposed adapters maintained by NautechSystems. No custom adapter needed.

### Running Two Adapters in One Engine

NT natively supports multiple execution clients per `LiveNode`. Each gets its own `ExecutionClient` with separate `account_id`, credentials, data subscriptions, and OMS.

```python
from nautilus_trader.live import LiveNode

node = LiveNode(config)
# OKX
okx_data = node.add_data_client(OKXDataClientConfig(...), OKXDataClientFactory())
okx_exec = node.add_execution_client(OKXExecutionClientConfig(
    account_id=AccountId("OKX-001"),
    instrument_types=[OKXInstrumentType.SWAP],
), OKXExecutionClientFactory())
# Lighter
lt_data = node.add_data_client(LighterDataClientConfig(
    deployment=LighterDeployment.Lighter,
    environment=LighterEnvironment.Mainnet,
), LighterDataClientFactory())
lt_exec = node.add_execution_client(LighterExecutionClientConfig(
    account_id=AccountId("LIGHTER-001"),
    account_index=..., api_key_index=4, private_key=...,
), LighterExecutionClientFactory())
```

**Settlement currency split**: OKX uses USDT; Lighter uses USDC. NT accounts are per-client, per-issuer — separate `AccountBalance` objects. No cross-venue margin netting. The strategy must track notional in a common unit (the arbTwin approach of computing all PnL in USD-equivalent via anchor prices).

### What a Custom Adapter Would Need (Not Required Here)

If Lighter lacked an adapter, a minimal custom `ExecutionClient` must implement:
- `LighterDataClient`: instrument discovery (`/api/v1/orderBookDetails`), book snapshots (REST 250 levels), WS `order_book` deltas, `market_stats` for mark/index/funding, `ticker` for BBO
- `LighterExecutionClient`: WS `sendTx`/`sendTxBatch` for create/modify/cancel; L2 signing (Schnorr over ECgFp5); nonce management (16-window, per-key); account streams (`account_all_positions`, `account_all_orders`, `account_all_trades`, `account_all_assets`, `user_stats`); REST reconciliation
- `LighterInstrumentProvider`: maps `market_index` to `InstrumentId`

Since both exist, this section is [NOT APPLICABLE].

## 3. Order Types & TIF

### OKX Adapter (Linear Perpetual Swap)

| Order Type | Support | Notes |
|---|---|---|
| `MARKET` | ✓ | Immediate execution |
| `MARKET_TO_LIMIT` | ✓ | Market→IOC limit |
| `LIMIT` | ✓ | |
| `STOP_MARKET` | ✓ | Via OKX algo orders (HTTP submit, WS status) |
| `STOP_LIMIT` | ✓ | Via OKX algo orders |
| `MARKET_IF_TOUCHED` | ✓ | Via OKX algo orders |
| `LIMIT_IF_TOUCHED` | ✓ | Via OKX algo orders |
| `TRAILING_STOP_MARKET` | ✓ | Via OKX advance algo orders |

| TIF | Support | Notes |
|---|---|---|
| `GTC` | ✓ | |
| `FOK` | ✓ | |
| `IOC` | ✓ | |
| `GTD` | – | No native GTD; use `StrategyConfig.manage_gtd_expiry=True` for local cancellation |

| Instruction | Support | Notes |
|---|---|---|
| `post_only` | ✓ | Limit orders only |
| `reduce_only` | ✓ | Net mode: literal `reduceOnly`; long/short mode: opposing side+posSide |
| `display_qty` (iceberg) | – | *Not supported by OKX* |
| Contingency (OCO/bracket) | – | `OKXExecutionClient` does not submit native OCO/bracket lists; use strategy-managed |
| RPI maker | ✓ | Via `params={"rpi": True, "rpi_px_round": True}` |
| RPI taker access | ✓ | Via `params={"rpi_taker_access": True}` |

### Lighter Adapter (Perpetuals)

| Order Type | Support | Notes |
|---|---|---|
| `MARKET` | ✓ | Cap from cached far-side quote + `market_order_slippage_bps` (default 50) |
| `LIMIT` | ✓ | |
| `STOP_MARKET` | ✓ | Perp only; cap from `trigger_price` + slippage |
| `STOP_LIMIT` | ✓ | Maps to Lighter stop-loss limit |
| `MARKET_IF_TOUCHED` | ✓ | Maps to Lighter take-profit market |
| `LIMIT_IF_TOUCHED` | ✓ | Maps to Lighter take-profit limit |
| `MARKET_TO_LIMIT` | – | *Not supported by Lighter* |
| `TRAILING_STOP_*` | – | *Not supported by Lighter* |

| TIF | Support | Notes |
|---|---|---|
| `GTC` | ✓ | Limit-style→`GoodTillTime`; market-style→`IOC`; 28-day fallback expiry |
| `IOC` | ✓ | Plain market/limit use expiry 0; conditional limit uses trigger expiry |
| `GTD` | ✓ | Via `use_gtd=true` (default); ≥5min+1s expiry; or use `manage_gtd_expiry` |
| `DAY` | ✓ | Limit/conditional only; 28-day fallback |
| `FOK` | – | *Not supported by Lighter* |

| Instruction | Support | Notes |
|---|---|---|
| `post_only` | ✓ | Maps to Lighter `PostOnly` TIF |
| `reduce_only` | ✓ | Perp only |
| `display_qty` (iceberg) | – | *Not supported by Lighter* |
| Contingency (OCO/OTO/OTOCO) | ✓ | **Native** via `CreateGroupedOrders`; OTO parent+OCO-linked SL/TP children; same-market only; mark-price triggers only |
| Self-trade prevention | ✓ | `expire_maker`/`expire_taker`/`cancel_both`/`reduce`; `account_index`/`master_account_index` scope |

### How to Express "Post-Only at Touch, Re-Peg on Timer, Escalate to IOC"

In NT, this is a **strategy-managed pattern**, not a single order type:

1. **Post-only at touch**: `self.order_factory.limit(..., post_only=True, price=passive_px(book))`
2. **Re-peg on timer**: `self.clock.set_time_alert(...)` → `on_time_event` handler calls `self.modify_order(order, price=new_touch)` (with same-price skip like arbTwin's `requote` logic)
3. **Escalate to IOC**: Cancel the post-only order, then submit new `LIMIT` at marketable price with `time_in_force=TimeInForce.IOC` — or, for Lighter, `IOC` TIF directly. OKX: `FOK` or `IOC` TIF.

**Lighter quirk**: conditional market orders reject `IOC` before trigger. IOC applies post-trigger only. For the hedge leg, use plain `MARKET` or `LIMIT` with `IOC`. Lighter market orders use `ImmediateOrCancel` wire TIF.

**OKX quirk**: market/conditional orders go through HTTP for algo (stop/trigger) types, not WS. The post-only leg is a regular WS `LIMIT` with `post_only=True`.

## 4. OMS Model

### `OmsType` Selection

| Strategy OMS | Venue OMS | Result |
|---|---|---|
| `NETTING` | `NETTING` | One position per instrument+strategy |
| `HEDGING` | `HEDGING` | Multiple positions per instrument+strategy |
| `NETTING` | `HEDGING` | One virtual position across venue positions |
| `HEDGING` | `NETTING` | Multiple virtual positions against venue's single net |

**Recommendation**: `HEDGING` for both strategy and venues. Each leg of the basis trade is a separate `Position` on a different venue (`AAPL-PERP.OKX` vs `AAPL-PERP.LIGHTER`). `HEDGING` keeps the two legs as distinct positions under one strategy, enabling per-leg tracking for the state machine.

OKX supports both net and long/short (hedging) position modes. Lighter uses one net position per perpetual market. With `Strategy OMS = HEDGING` and `OKX OMS = HEDGING` (long/short mode) vs `Lighter OMS = NETTING`, NT creates multiple virtual positions on OKX and one net position on Lighter — the strategy tracks both under `HEDGING`.

**Position IDs**: under `HEDGING`, custom `position_id` values are valid. Use deterministic IDs like `{symbol}-okx-leg` and `{symbol}-lighter-leg` to map legs.

### Position Reconciliation

NT's `ExecutionEngine` handles:
- **Startup reconciliation**: `ExecutionReconciliation` aligns cache with venue state. The Lighter adapter replays `subscribed/account_all_positions` snapshot; OKX queries REST positions. Unknown positions become reconciliation orders.
- **Fill processing**: `OrderFilled` → `Position` opened/changed/closed.
- **`PositionStatusReport`**: venue-authoritative position state; Lighter includes `liquidation_price`.

The arbTwin `reconcile.py` (delta-based, disk-persisted, poll-driven) is replaced by NT's built-in reconciliation. The disk ledger (`state/positions.json`) is unnecessary — NT's cache is event-sourced and durable.

### Strategy Event Handlers

Key handlers for the basis state machine:

```python
def on_start(self) -> None:          # Load instruments, subscribe
def on_stop(self) -> None:           # Cancel all, close positions
def on_quote(self, tick: QuoteTick) -> None:  # Update basis, trigger decisions
def on_order_filled(self, event: OrderFilled) -> None:  # Leg filled → state transition
def on_order_accepted(self, event: OrderAccepted) -> None:  # Quote resting
def on_order_rejected(self, event: OrderRejected) -> None:  # Post-only crossed → reject
def on_order_canceled(self, event: OrderCanceled) -> None:  # Leg canceled
def on_position_opened(self, event: PositionOpened) -> None:  # Entry confirmed
def on_position_changed(self, event: PositionChanged) -> None:  # Partial fill
def on_position_closed(self, event: PositionClosed) -> None:  # Exit complete
def on_time_event(self, event: TimeEvent) -> None:  # Poll/re-peg timer
```

The arbTwin state machine (`RoundState`: FLAT→ENTRY→ENTRY_PARTIAL→HEDGED→EXIT_*) maps to strategy-level state tracked via position IDs and order statuses.

### Actor vs Strategy

- Use `Strategy` (not `Actor`): `Actor` lacks order management. The basis engine submits/modifies/cancels orders.
- `StrategyConfig`: set `oms_type=OmsType.HEDGING`, `manage_contingent_orders=False` (native groups on Lighter), `manage_gtd_expiry=True` (for OKX's lack of native GTD), `use_hyphens_in_client_order_ids=False` (OKX restriction).

## 5. Risk Engine

### NT `RiskEngine` Pre-Trade Checks

Unless `bypass=True`, the `RiskEngine` validates:

| Check | Detail |
|---|---|
| Price precision | Matches instrument `price_precision` |
| Quantity precision | Matches instrument `size_precision` |
| Positive prices | Unless instrument allows negative |
| `min_notional` / `max_notional` | Per-instrument bounds |
| `max_notional_per_order` | Engine-level per-instrument cap (HashMap) |
| `reduce_only` exposure | Order must not increase position |
| Cash balance | For non-margin accounts |
| Submit/modify rate limits | `max_order_submit` / `max_order_modify` |
| Trading state | `ACTIVE`→all; `REDUCING`→reduce-only; `HALTED`→cancel/query only |

### What Must Stay Custom

NT's `RiskEngine` handles **structural** pre-trade checks. The following arbTwin-specific gates must stay in strategy code:

| arbTwin Gate | Location in NT |
|---|---|
| Basis band (`band.py`) | Strategy: compute before `submit_order` |
| Mechanics (`mechanics.py`): mark window, OI utilization, depth density, liq distance | Strategy: `on_quote` handler + periodic REST depth snapshots |
| Anchor (`anchor.py`): session freeze, index agreement | Strategy: `on_start` + clock checks |
| Issuer risk (`issuer_risk.py`) | Strategy: `on_start` symbol screening |
| Manipulation cost (`manipulation.py`) | Strategy: periodic depth analysis |
| Naked-leg limits (`max_unhedged_notional_usd`, `adverse_move_stop`) | Strategy: `on_order_filled` + `on_time_event` |
| Exit queue (`exitqueue.py`) | Strategy: state machine with timer-driven escalation |
| Fee book (`fees.py`) | Strategy: compute before entry/exit |
| Hedge failure cooldown | Strategy: `hedge_failure_count` + cooldown timer |

## 6. Synthetic Instruments

### NT `SyntheticInstrument`

NT's formula engine computes derived prices from component ticks:

```python
from nautilus_trader.model import SyntheticInstrument

synthetic = SyntheticInstrument(
    symbol=Symbol("AAPL-BASIS"),
    price_precision=2,
    components=[
        InstrumentId.from_str("AAPL-PERP.LIGHTER"),
        InstrumentId.from_str("AAPL-USDT-SWAP.OKX"),
    ],
    formula="AAPL-PERP.LIGHTER - AAPL-USDT-SWAP.OKX",
    ts_event=self.clock.timestamp_ns(),
    ts_init=self.clock.timestamp_ns(),
)
self.add_synthetic(synthetic)
self.subscribe_quotes(synthetic.id)
```

**Can it represent "Lighter price − OKX price"?** Yes. The formula `AAPL-PERP.LIGHTER - AAPL-USDT-SWAP.OKX` produces a synthetic quote tick whenever either component updates (using the other's last cached value). The strategy's `on_quote` handler receives the basis as `QuoteTick`.

**Can it drive subscriptions?** Yes. `self.subscribe_quotes(synthetic.id)` delivers derived quotes. But the strategy must also subscribe to raw quotes from both venues for book-level data (depth, mechanics gates).

**Limitations**:
- The formula operates on **prices**, not basis points. The strategy must convert to bps using a reference price (anchor).
- Synthetic quotes don't carry order book depth — depth gates and mechanics checks need raw venue subscriptions.
- USDT vs USDC: the formula does a direct numeric subtraction of two prices in different quote currencies. This is acceptable when `unit_ratio ≈ 1.0` (proven by calibration), but the strategy must verify calibration before trusting synthetic values.

**Recommendation**: Use `SyntheticInstrument` for monitoring/pricing triggers, but the strategy should compute `basis_bps` internally for entry decisions (as arbTwin does with `spread.py`). The synthetic is an auxiliary feed, not the sole decision input.

## 7. Module-by-Module Mapping

See `mapping.md` for the full table. Summary:

| arbTwin Module | NT Component | Notes |
|---|---|---|
| `engine.py` (TwinEngine) | `Strategy` subclass + `LiveNode` | poll loop → event-driven; `execute_best` → `on_quote` + state machine |
| `wave.py` (RoundState) | Strategy state machine (internal enum) | Event handlers replace `decide()` |
| `venues/okx.py` | `OKXDataClient` + `OKXExecutionClient` | Built-in adapter |
| `venues/lighter.py` | `LighterDataClient` + `LighterExecutionClient` | Built-in adapter |
| `venues/lighter_trade.py` | Lighter adapter's L2 signer | Handled internally by `nautilus-lighter` |
| `spread.py` | Strategy `on_quote` + internal compute | `SyntheticInstrument` optional |
| `calibration.py` | Pre-startup script or `on_start` historical request | Run before live, not per-tick |
| `mechanics.py` | Strategy depth analysis | REST snapshots via adapter, TTL-managed |
| `band.py` | Strategy internal | Computed per-tick from σ_basis |
| `fees.py` | Strategy internal | Config-driven, venue-specific |
| `anchor.py` | Strategy `on_start` + clock | Session freeze → trading state check |
| `exitqueue.py` | Strategy state machine | Timer-driven escalation via `set_time_alert` |
| `reconcile.py` | NT `ExecutionEngine` reconciliation | Built-in, event-sourced |
| `wavetrader.py` | `OrderFactory` + order tracking in cache | `self.cache.orders()` replaces `WaveTrader.quotes` |
| `monitor.py` | `on_quote` + logging + webhook | Signal-based alerting |
| `screening.py` | Pre-startup script | Not in live strategy |
| `config/*.yaml` | `StrategyConfig` + `*ClientConfig` | Python config classes |
| `data.py` | `ParquetDataCatalog` | NT native catalog |
| `backtest/replay.py` | `BacktestNode` + `BacktestVenueConfig` | NT built-in backtester |

### Backtest with `ParquetDataCatalog`

```python
from nautilus_trader.config import BacktestDataConfig, DataCatalogConfig

data_config = BacktestDataConfig(
    catalog=DataCatalogConfig(
        path="/path/to/catalog",
        backend=CatalogBackend.Parquet,
    ),
    ...
)
```

NT's `BacktestNode` replays historical data from Parquet. Need to:
1. Record OKX + Lighter quote/trade ticks into Parquet via `ParquetDataCatalog`
2. Configure `BacktestVenueConfig` for each venue with matching OMS type
3. Trade execution: NT backtest venue uses `OrderMatchingEngine` with configurable fill models. **Maker fill fidelity**: backtest venue simulates fills when the book crosses the resting price — same conservative model arbTwin uses. Queue position is not simulated (same limitation). Custom `FillModel` or `LatencyModel` available via Python subclassing (v2.0.0rc8: "Added custom Python fill models").

### Live Config

```python
from nautilus_trader.live import LiveNode, LiveNodeConfig
from nautilus_trader.config import LiveExecutionEngineConfig

live_config = LiveNodeConfig(
    execution_engine=LiveExecutionEngineConfig(
        reconciliation_instrument_ids=[...],  # Scope reconciliation
        reconciliation_lookback_mins=1440,
    ),
)
node = LiveNode(live_config)
node.add_strategy_from_config(BasisEngineConfig(...))
node.run()
```

## 8. Prioritised Implementation Plan

### P0 — Foundation (weeks 1–2)
1. **`BasisEngineConfig`** (`nautilus_basis/config.py`): strategy config with symbols, risk limits, band params, fee schedules, anchor sessions
2. **`BasisEngine`** (`nautilus_basis/strategy.py`): `Strategy` subclass with state machine (FLAT→ENTRY→HEDGED→EXIT→FLAT); `on_start` loads instruments, subscribes quotes+books; `on_quote` computes basis, gates, decisions
3. **Symbol screening** (`nautilus_basis/screening.py`): pre-startup calibration script using historical bar data; writes approved symbol list to config
4. **Dry-run** backtest on 1 symbol (AAPL) with `ParquetDataCatalog` replaying recorded quote ticks; verify state transitions

### P1 — Execution (weeks 3–4)
5. **Post-only entry**: `on_quote` → submit LIMIT post-only on leading leg → `on_order_accepted` → submit IOC marketable on hedge leg
6. **Re-peg timer**: `set_time_alert` every N seconds → `on_time_event` → `modify_order` if price changed (skip if same px)
7. **Escalation to IOC**: timer-based escalation in exit queue; cancel resting → submit IOC at frozen exit price
8. **Naked-leg abort**: `on_order_filled` (one leg only) → start abort timer → `on_time_event` → passive unwind of filled leg; `reduce_only` + bounded adverse move stop
9. **Risk gates**: implement all arbTwin mechanics gates in strategy; `max_unhedged_notional`, depth density, mark divergence, OI utilization

### P2 — Robustness (weeks 5–6)
10. **Multi-symbol**: fan-out across approved symbols; `max_open_symbols` limit in strategy
11. **Orphan detection**: `on_start` queries positions; non-zero → CRITICAL alert, block new entries on that symbol
12. **LiveNode deployment**: systemd service, health checks, webhook alerts
13. **Tick-level recording**: record quotes+trades to Parquet for backtest fidelity analysis; measure `capture_fraction` (p_fill at p95 events)

### P3 — Polish (ongoing)
14. **Fee model**: custom Python fee model per venue, accounting for incentive periods
15. **Latency model**: custom latency model for Lighter 300ms speed bump in backtests
16. **RPI integration**: OKX RPI maker orders for tighter spreads when eligible

### File/Class Names

```
nautilus_basis/
├── __init__.py
├── config.py          # BasisEngineConfig, BasisRiskConfig, BasisFeeConfig
├── strategy.py        # BasisEngine(Strategy)
├── state.py           # RoundState enum, Leg dataclass
├── gates.py           # MechanicsBook, GateVerdict, evaluate_venue
├── band.py            # entry_band_bps, exit_band_bps, max_position_usd
├── fees.py            # FeeBook, build_fee_book
├── anchor.py          # AnchorBook, Session
├── screening.py       # pre-startup calibration script
├── calibration.py     # calibrate, CalibrationResult (unit ratio, corr)
├── exitqueue.py       # ExitQueue, ExitTicket
├── monitor.py         # Webhook alerts, JSONL logging
└── reconciliation.py  # Startup orphan check (thin wrapper)
```

### Event Flow (One Round)

```
on_quote(tick) → compute basis_bps → gate check → decide ENTRY
  → submit_order(okx_leg: LIMIT post_only) → OrderAccepted
  → submit_order(lt_leg: LIMIT IOC marketable) → OrderFilled | OrderRejected
  [if both filled] → PositionOpened(okx) + PositionOpened(lt) → state=HEDGED
  [if one filled] → on_order_filled → start abort timer → state=ENTRY_PARTIAL
    → on_time_event(abort) → cancel_order(empty_leg)
    → submit_order(filled_leg: LIMIT post_only reduce_only) → passive unwind
exit_trigger (basis decay or PnL target) → state=EXIT
  → submit_order(okx_leg: LIMIT post_only reduce_only, freeze_px)
  → submit_order(lt_leg: LIMIT post_only reduce_only, freeze_px)
  → on_time_event(escalate) → cancel_order → submit_order(LIMIT IOC reduce_only)
  → both filled → PositionClosed(okx) + PositionClosed(lt) → state=FLAT
```

## 9. Gaps & Risks

### Adapter Limitations

| Issue | Risk | Mitigation |
|---|---|---|
| **OKX passphrase** | NT adapter uses API key+secret+passphrase — all standard. OKX `OKXExecutionClientConfig` accepts `api_key`, `api_secret`, `passphrase` | Works. Credentials via env vars: `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE` [INFERENCE from standard OKX auth] |
| **Lighter nonce streams** | NT adapter manages 16-window nonce per API key. Nonce recovery on reconnect via `GET /api/v1/nextNonce`. Multi-key not directly exposed — one `api_key_index` per client. | Use separate `LighterExecutionClient` instances for separate keys if needed (post-only vs taker). The arbTwin multi-key pattern (4=maker, 5=taker) is not natively expressible — [INFERENCE] one client per key requires separate `ExecutionClient` instances |
| **USDC vs USDT accounts** | Separate NT accounts per venue. No cross-venue margin. Strategy must track notional in common unit. | The basis engine's calibration already proves `unit_ratio ≈ 1.0` for approved symbols. PnL computed in USD-equivalent via anchor. |
| **OKX no native GTD** | `GTD` TIF not supported. Use `StrategyConfig.manage_gtd_expiry=True`. | Local cancellation at expiry; venue latency may delay removal |
| **Lighter no FOK** | `FOK` TIF not supported. Use `IOC` instead. | IOC with full-quantity check after fill is equivalent for the hedge leg |
| **No iceberg/display_qty** | Neither venue supports it. | Not needed for basis engine; all orders are full-display |
| **OCO not native on OKX** | OKX adapter does not submit native OCO lists. | Use strategy-managed OCO (cancel sibling on fill) |
| **Lighter 5-min GTD minimum** | `use_gtd=true` requires ≥5min+1s expiry. | Shorter lifetimes use `manage_gtd_expiry=true` |
| **Lighter modification rate limit** | ~40 mods/min on Standard tier (transaction type limit). | Same-price re-peg skip prevents nonce waste; wider refresh thresholds |
| **OKX conditional orders HTTP-only** | Stop/trigger orders go via HTTP, not WS. | The strategy uses plain LIMIT orders (not conditional) for legs — not affected |

### Backtest Fidelity

| Concern | Status |
|---|---|
| **Maker fill simulation** | NT backtest venue fills when book crosses resting price — same conservative model as arbTwin's `WaveTrader.poll_fills`. Queue position not simulated. |
| **Lighter 300ms speed bump** | Custom `LatencyModel` (Python-subclassable in 2.0.0rc8) can introduce delay. |
| **Partial fills** | `OrderMatchingEngine` supports partial fills based on available liquidity at price level. |
| **Tick-level recording** | `ParquetDataCatalog` can store quote ticks, trade ticks, and book snapshots from both venues. |
| **No tick data → bars only** | NT backtest supports bar execution (OHLC) with configurable price simulation. Maker fills on bars are coarser than tick-level. |
| **`capture_fraction` unmeasured** | arbTwin §6: p95 fill probability 10–50%, 1h regression 3.4–9.6bps. NT backtest with tick data could measure this — prerequisite for realistic fill assumptions. |

### Unsupported arbTwin Features

| Feature | NT Equivalent | Status |
|---|---|---|
| `max_unhedged_ms` (hard time bound) | Strategy timer + abort logic | Implementable |
| Frozen exit prices (`freeze_px_on_trigger`) | Capture price at trigger in strategy state | Implementable |
| Exit queue with price tiers (0/2/5 bps) | Strategy state machine with tier escalation | Implementable |
| Lighter nonce per-key deduplication | Adapter handles nonces; multi-key needs multiple clients | [INFERENCE] Requires separate `LighterExecutionClient` per key |
| `rounds.jsonl` persistence | NT event-sourced cache is durable; `on_save`/`on_load` for custom state | Replaces file-based persistence |
| Restart orphan detection | `on_start` position query | Implementable |

### Critical Unknowns (require live testing)

1. **Lighter multi-key**: Can one `LiveNode` host two `LighterExecutionClient` instances (different API key indexes, same account) without nonce conflicts? The adapter uses one nonce window per client — two clients sharing one account need careful nonce coordination. [INFERENCE: not yet tested]
2. **OKX `instIdCode`**: The adapter requires `instIdCode` (numeric instrument ID) at startup. All configured instrument types must resolve. This is a startup-time check — no runtime risk if symbols are pre-validated.
3. **Cross-venue timing**: Quote ticks from two venues arrive asynchronously. The strategy's `on_quote` handler for one venue needs the other's last cached quote. This works, but adds 1 tick of staleness to the faster venue's data.
4. **Lighter scheduled cancellation**: `scheduled_cancel_timeout_secs` enables account-wide dead-man's switch. This could interfere with the strategy's resting orders if not coordinated.