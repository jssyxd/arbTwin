# arbTwin → NautilusTrader Module Mapping

| arbTwin Module | arbTwin Class/Function | NT Component | NT Class / API | Status | Notes |
|---|---|---|---|---|---|
| `engine.py` | `TwinEngine` | Strategy + LiveNode | `Strategy` subclass, `LiveNode.add_strategy_from_config()` | Rewrite | Poll loop → event-driven; `execute_best` → `on_quote` handler |
| `engine.py` | `Broker` | Execution clients | `OKXExecutionClient` + `LighterExecutionClient` | Replace | Built-in adapters handle submit/modify/cancel |
| `engine.py` | `SymbolState` | Strategy state + cache | `self.cache.instrument()`, `self.cache.positions()` | Rewrite | NT cache is authoritative |
| `engine.py` | `MakerQuote` | Order cache | `self.cache.orders()` | Replace | NT tracks orders with full lifecycle |
| `engine.py` | `_gate_symbol` | Strategy method | Custom method in strategy | Rewrite | Same logic, called from `on_quote` |
| `wave.py` | `RoundState` | Strategy state enum | Internal `Enum` in strategy | Adapt | Same states, event-driven transitions |
| `wave.py` | `WaveParams` | StrategyConfig | `BasisEngineConfig` fields | Adapt | Parameters from YAML/env → NT config class |
| `wave.py` | `Leg` | Position tracking | `Position` objects via cache | Replace | `self.cache.positions()` per instrument |
| `wave.py` | `Round` | Strategy state | Instance variables in strategy | Adapt | Per-symbol state dict |
| `wave.py` | `decide()` | Event handlers | `on_quote`, `on_order_filled`, `on_time_event` | Rewrite | Pure function → event dispatch |
| `wave.py` | `_entry_orders()` | OrderFactory | `self.order_factory.limit(post_only=...)` | Adapt | Same price logic, NT order construction |
| `wave.py` | `_requote()` | modify_order | `self.modify_order(order, price=new_px)` | Adapt | Same-price skip check |
| `wave.py` | `_abort()` | Strategy method | Custom abort logic | Rewrite | cancel empty leg → passive unwind filled leg |
| `wave.py` | `on_fill()` | Event handler | `on_order_filled` | Adapt | NT delivers `OrderFilled` with qty/px |
| `wave.py` | `passive_px()` | Strategy helper | Same function | Keep | Pure; sell→ask, buy→bid |
| `wave.py` | `marketable_px()` | Strategy helper | Same function | Keep | Pure; sell→bid, buy→ask |
| `wavetrader.py` | `WaveTrader` | OrderFactory + cache | `self.order_factory`, `self.cache.orders()` | Replace | NT handles order lifecycle |
| `wavetrader.py` | `RestingQuote` | Order cache entry | `self.cache.order(client_order_id)` | Replace | NT `Order` with full state |
| `wavetrader.py` | `poll_fills()` | Event handler | `on_order_filled` | Replace | Event-driven, not polled |
| `wavetrader.py` | `step()` | Event handler | `on_quote` + state machine | Rewrite | Pure logic moves to event handlers |
| `wavetrader.py` | `_drop_quotes()` | cancel_order | `self.cancel_order(order)` | Replace | NT handles cancellation lifecycle |
| `venues/okx.py` | `OkxVenue` | OKX adapter | `OKXDataClient` + `OKXExecutionClient` | Replace | Official, Rust-native, PyO3 |
| `venues/okx.py` | `_OkxWs` | OKX WebSocket | Internal to adapter | Replace | `books5`, `bbo-tbt` channels |
| `venues/okx.py` | `quote()` | Quote subscription | `self.subscribe_quotes(instrument_id)` | Replace | `on_quote` handler receives `QuoteTick` |
| `venues/okx.py` | `book()` | Book snapshot | `self.request_book_snapshot(instrument_id)` | Replace | REST 400-level snapshot |
| `venues/okx.py` | `instruments()` | Instrument provider | Automatic via `OKXDataClientConfig` | Replace | Adapter loads `public/instruments` |
| `venues/okx.py` | `positions()` | Position query | `self.cache.positions()` or adapter query | Replace | WebSocket `account` channel |
| `venues/lighter.py` | `LighterVenue` | Lighter adapter | `LighterDataClient` + `LighterExecutionClient` | Replace | Official, Rust-native, PyO3 |
| `venues/lighter.py` | `_LighterWs` | Lighter WebSocket | Internal to adapter | Replace | `order_book`, `market_stats`, `ticker` |
| `venues/lighter.py` | `quote()` | Quote subscription | `self.subscribe_quotes(instrument_id)` | Replace | `on_quote` handler |
| `venues/lighter.py` | `book()` | Book snapshot | `self.request_book_snapshot(instrument_id)` | Replace | REST 250-level snapshot |
| `venues/lighter_trade.py` | `LighterTradeClient` | Lighter adapter | Internal to `nautilus-lighter` | Replace | L2 signing, nonce management, `sendTx` |
| `venues/lighter_trade.py` | `AsyncBridge` | Lighter adapter | Internal async runtime | Replace | Adapter handles async/sync bridging |
| `venues/lighter_trade.py` | `DryTradeClient` | Sandbox execution | `SandboxExecutionClient` | Replace | NT sandbox for testing |
| `venues/base.py` | `VenueAdapter` | Adapter traits | `DataClient` + `ExecutionClient` traits | Replace | NT's Rust trait system |
| `venues/base.py` | `ThreadedWs` | WebSocket client | Internal to adapters | Replace | Reconnecting WS with recovery |
| `spread.py` | `compute_row()` | Strategy method | `on_quote` handler | Adapt | Same computation, event-driven |
| `spread.py` | `build_plan()` | Strategy method | Entry decision in `on_quote` | Adapt | Two-leg plan → two `submit_order` calls |
| `spread.py` | `summarise()` | Logging | `self.log.info(...)` | Adapt | Structured logging |
| `calibration.py` | `calibrate()` | Pre-startup script | Standalone script using `request_bars` | Adapt | Historical bar analysis |
| `calibration.py` | `CalibrationResult` | Config | `BasisEngineConfig.symbols[].calibration` | Adapt | Stored in config, not runtime |
| `mechanics.py` | `MechanicsBook` | Strategy helper | `Gates` class in strategy | Adapt | Same logic, uses NT book/cache |
| `mechanics.py` | `build_mechanics()` | Strategy method | Called from `on_start` + periodic timer | Adapt | Depth snapshots via REST |
| `mechanics.py` | `evaluate_venue()` | Strategy method | `on_book` or depth analysis | Adapt | Gate verdict → allow/deny entry |
| `anchor.py` | `AnchorBook` | Strategy helper | `Anchor` class in strategy | Adapt | Session-aware anchor |
| `anchor.py` | `Session` | Strategy helper | Time-based checks | Adapt | `is_open(now)` → trading state |
| `band.py` | `entry_band_bps()` | Strategy method | Pure function | Keep | Same formula, in strategy |
| `band.py` | `exit_band_bps()` | Strategy method | Pure function | Keep | Same formula |
| `band.py` | `max_position_usd()` | Strategy method | Pure function | Keep | Same formula |
| `band.py` | `sigma_1h_bps()` | Strategy method | `on_bar` + rolling window | Adapt | Compute from bar data |
| `fees.py` | `FeeBook` | Strategy config | `BasisFeeConfig` | Adapt | Venue × order type × incentive |
| `fees.py` | `build_fee_book()` | Config loading | Config constructor | Adapt | From YAML/env → NT config |
| `exitqueue.py` | `ExitQueue` | Strategy state machine | Timer-driven escalation | Rewrite | `set_time_alert` → escalate tier |
| `exitqueue.py` | `ExitTicket` | Strategy state | Instance variable | Adapt | Per-symbol exit state |
| `issuer_risk.py` | `build_card()` | Pre-startup script | Standalone screening | Keep | Same logic |
| `manipulation.py` | `profile()` | Strategy helper | Depth analysis method | Keep | Same logic |
| `reconcile.py` | `PositionLedger` | ExecutionEngine | NT built-in reconciliation | Replace | Event-sourced; no file-based ledger |
| `reconcile.py` | `FillEvent` | OrderFilled event | `OrderFilled` model | Replace | NT native event |
| `reconcile.py` | `observe()` | ExecutionEngine | Automatic via WS account streams | Replace | Venue-authoritative position deltas |
| `reconcile.py` | `seed()` | Reconciliation | `ExecutionReconciliation` at startup | Replace | NT handles startup state alignment |
| `monitor.py` | Monitor | Signals + logging | `on_quote` logging + webhook | Adapt | `self.log.info()` + `requests.post` |
| `data.py` | Historical data | ParquetDataCatalog | `ParquetDataCatalog` | Replace | NT native catalog |
| `screening.py` | Universe funnel | Pre-startup script | Standalone `screen.py` | Keep | Same logic, writes to config |
| `config/*.yaml` | AppConfig | NT config classes | `BasisEngineConfig`, `*ClientConfig` | Rewrite | Python dataclasses, not YAML |
| `config/venues.yaml` | Venue config | Adapter config | `OKXExecutionClientConfig`, `LighterExecutionClientConfig` | Replace | Per-adapter config objects |
| `config/risk.yaml` | Risk limits | Strategy config + RiskEngine | `BasisRiskConfig` + `RiskEngineConfig` | Adapt | Split: structural → RiskEngine; mechanical → strategy |
| `config/symbols.yaml` | Symbol list | Strategy config | `BasisEngineConfig.symbols` | Adapt | List of approved symbols with params |
| `backtest/replay.py` | Backtest | BacktestNode | `BacktestNode` + `BacktestVenueConfig` | Replace | NT built-in backtester |
| `backtest/replay.py` | Walk-forward | Backtest | `BacktestNode` with date ranges | Replace | NT supports time-range backtests |
| `tests/` | 46 logic tests | NT test framework | `pytest` + `BacktestNode` | Adapt | Strategy tests via backtest engine |