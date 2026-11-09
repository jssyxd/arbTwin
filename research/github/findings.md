# Open-Source Execution/Rebalance Patterns for Two-Leg Basis Trading

> Research date: 2026-11-09. Query: `W:/dynamicTWIN` (arbTwin).  
> All findings verified against source via GitHub API; [INFERENCE] where code not fully read.

---

## 1. Top Recommendations (Ranked)

| Rank | Repo | Why |
|---|---|---|
| 1 | **pranay123-stack/perp-funding-basis-arb** | Most complete two-leg hedge cycle with partial-fill handling, rebalance, unwind, patient exit, and position reconciliation. Paper-only but the state machine and execution patterns are directly transferable. |
| 2 | **counterfactual5/funding-arb** | Production-grade cross-venue execution with actual Lighter + OKX venue adapters, pure-futures spread executor with rollback, settle-mismatch planner for funding-period gaps. Actively maintained (pushed 2026-11-09). |
| 3 | **nautechsystems/nautilus_trader** | Production Rust-native engine with OMS, position management, order emulator, and multi-venue adapters (OKX, Bybit, Hyperliquid). No ready-made two-leg spread strategy found in examples, but the infrastructure for building one exists. Best choice if porting arbTwin to a professional event-driven architecture. |
| 4 | **hummingbot/hummingbot** | Cross-exchange ArbitrageExecutor pattern (market-order both legs simultaneously), rate oracle for quote-asset conversion. Simple but the two-leg execution pattern is too basic for limit-order basis trading. |
| 5 | **freqtrade/freqtrade** | Single-exchange only. Not relevant for cross-venue basis. |
| 6 | **jesse-ai/jesse** | Strategy framework but single-exchange. No multi-venue hedging. |
| 7 | **enarjord/passivbot** | Grid/DCA market maker with OKX support. Rebalance limited to single-venue position management. |

---

## 2. Repo Table

| Repo | Stars/Updated | License | What to Reuse | Link |
|---|---|---|---|---|
| pranay123-stack/perp-funding-basis-arb | 0★ / 2026-09-25 | MIT | HedgeCycle state machine, partial-fill resizing, rebalance, unwind, patient exit, reconciliation, risk engine | [GitHub](https://github.com/pranay123-stack/perp-funding-basis-arb) |
| counterfactual5/funding-arb | 11★ / 2026-11-09 | MIT | CrossVenueExecutor with rollback, pure-futures spread executor, Lighter+OKX venue adapters, settle-mismatch planner | [GitHub](https://github.com/counterfactual5/funding-arb) |
| nautechsystems/nautilus_trader | 31,400★ / 2026-11-09 | LGPL-3.0 | OMS, position netting, order emulator, OKX/Hyperliquid/Bybit adapters, event-driven architecture | [GitHub](https://github.com/nautechsystems/nautilus_trader) |
| hummingbot/hummingbot | 21,108★ / 2026-11-09 | Apache-2.0 | ArbitrageExecutor (market-order both legs), RateOracle for quote conversion, ConnectorPair model | [GitHub](https://github.com/hummingbot/hummingbot) |
| hypurrquant/perp-cli | 41★ / 2026-11-06 | ? | Multi-DEX CLI including Lighter, HIP-3 support, MCP server for AI agents | [GitHub](https://github.com/hypurrquant/perp-cli) |
| R1cK-ChaN/crypto-funding-arbitrage | 16★ / 2026-10-24 | ? | OKX market data + spot-perp signal analysis (research prototype) | [GitHub](https://github.com/R1cK-ChaN/crypto-funding-arbitrage) |
| Flotapponnier/funding-radar | 1★ / 2026-05-05 | ? | Funding rate divergence scanner across 7 venues including Lighter | [GitHub](https://github.com/Flotapponnier/funding-radar) |
| enarjord/passivbot | 2,159★ / 2026-11-09 | MIT | OKX venue support, position management (single-venue rebalance only) | [GitHub](https://github.com/enarjord/passivbot) |

---

## 3. Code-Path Notes

### 3.1 pfba: HedgeCycle — the aggregate root (`backend/src/perparb/engine/hedge_cycle.py`)

The two-leg trade is one `HedgeCycle` owning two `CycleLeg` objects:

```python
@dataclass(slots=True)
class CycleLeg:
    leg: str          # "long" | "short"
    venue: Venue
    symbol: str
    side: Side
    target_quantity: Decimal
    filled_quantity: Decimal = ZERO
    average_price: Decimal = ZERO
    fees_paid: Decimal = ZERO
    # ... exit tracking, funding payments
```

Key property: `HedgeCycle.hedge_ratio()` — computed from `base_exposure()` on each leg, not from raw quantities. This is venue-independent (contract multipliers, inverse contracts handled):

```python
# hedge_cycle.py: hedge_ratio()
def hedge_ratio(self) -> Decimal:
    long_exp = abs(self.long_leg.base_exposure()) if self.long_leg else ZERO
    short_exp = abs(self.short_leg.base_exposure()) if self.short_leg else ZERO
    if short_exp <= 0:
        return Decimal("Infinity")
    return long_exp / short_exp

def is_hedge_intact(self, target_ratio, tolerance) -> bool:
    actual = self.hedge_ratio()
    return abs(actual - target_ratio) <= tolerance
```

**Takeaway for arbTwin**: arbTwin's `wave.py` tracks quantities directly. Adding `base_exposure()` and `hedge_ratio()` would make multi-instrument rebalancing correct when contract specs differ.

### 3.2 pfba: ExecutionEngine.advance() (`backend/src/perparb/engine/execution.py:332-466`)

The multi-tick entry state machine:

```
LEG_A_SUBMITTED → poll fills → re-peg passive → timeout? → cross → 
  LEG_A_PARTIAL (partial fill) → size leg B to match → LEG_B_SUBMITTED
  LEG_A_FILLED → submit leg B (always crosses)
LEG_B_SUBMITTED → poll fills → 
  LEG_B_PARTIAL → DEGRADED (rebalance needed) or cross to complete
  LEG_B_FILLED → HEDGED → MONITORING
```

Critical design decisions visible in the code:

1. **Leg ordering**: `ExecutionEngine.order_legs()` → `choose_lead_leg()` — harder leg (thinner book, slower venue) goes first. The second leg crosses immediately. This bounds unhedged exposure to one round trip.

2. **Passive entry re-peg** (`_repeg`, line 287-330): Resting limit orders are re-priced to the current touch every tick. "A limit order left where it was placed is not a quote, it is a landmine."

3. **Partial fill on leg A** (line 404-412): Leg B is sized down to match what leg A actually acquired:
   ```python
   # execution.py: _match_quantity (called via _submit_second with resize=True)
   def _match_quantity(self, cycle, first, second):
       first_base = abs(first.base_exposure())
       second_inst = self.registry.get(second.venue, second.symbol)
       # Derive second leg quantity from first leg's actual base exposure
       return second_inst.quantity_for_base_exposure(
           first_base * self.strategy.sizing.target_hedge_ratio, price
       )
   ```

4. **Partial fill on leg B** (line 462-464): Goes DEGRADED. The `rebalance()` method (line 699-757) either tops up the under-filled leg or trims the over-filled one.

5. **Leg B rejection** (line 454-455): Unwinds leg A immediately.

6. **Hedging leg always crosses** (line 484-489 comment): "Resting it leaves the book directionally exposed for however long the passive order takes to fill... showed up as thousands of dollars of 'price P&L' on hedges that were flat to four decimals."

### 3.3 pfba: Rebalance (`execution.py:699-757`)

```python
async def rebalance(self, cycle: HedgeCycle) -> HedgeCycle:
    # Determines which leg is under-filled by comparing base exposures
    if short_exp < long_exp * target_ratio:
        under, over = cycle.short_leg, cycle.long_leg
        needed_base = long_exp * target_ratio - short_exp
    else:
        under, over = cycle.long_leg, cycle.short_leg
        needed_base = short_exp / target_ratio - long_exp
    
    top_up = inst.clamp_quantity(inst.quantity_for_base_exposure(needed_base, price))
    if top_up > 0:
        await self._submit(cycle, under, top_up, is_exit=False)  # add to under-filled
    else:
        # Cannot add → trim the larger leg to match
        trim = over_inst.clamp_quantity(...)
        await self._submit(cycle, over, trim, is_exit=True)  # close excess
```

**Key insight**: rebalance always tries to *add* before *trimming*. Trimming realises costs; adding earns carry. Only trims when top-up is below venue minimum.

### 3.4 pfba: Patient vs Urgent Exit (`execution.py:819-973`)

```python
URGENT_EXITS = frozenset({
    ExitReason.STOP_LOSS, ExitReason.RISK_LIMIT, ExitReason.EMERGENCY,
    ExitReason.MAX_HOLD_TIME, ExitReason.BASIS_DIVERGED,
    ExitReason.FUNDING_REVERSAL, ExitReason.MANUAL,
})

def _exit_is_patient(self, reason: ExitReason) -> bool:
    return self.strategy.costs.exit_is_maker and reason not in self.URGENT_EXITS
```

Patient exit: only the lead leg rests passively (maker); the hedge leg crosses as soon as lead leg closes. Exit timeout → cross both. Matches arbTwin's exitqueue ladder pattern (0/2/5 bps → IOC escalation).

> "Resting both exit legs looks cheaper on the fee line and is not: the moment the first one fills the position is naked, and the resulting directional exposure dwarfs the fee it saved."

### 3.5 pfba: Reconciliation (`backend/src/perparb/engine/reconciliation.py`)

Three-way reconciliation: database ↔ venue ↔ in-memory cycles. Rule: **venue is the authority on positions**. Mismatch classification: PHANTOM, MISSING, QUANTITY, ORPHAN, UNEXPLAINED.

**Takeaway for arbTwin**: arbTwin currently uses "polling + price crossover" for fill detection. Adding a reconciliation pass that queries actual venue positions would catch orphan fills from cancelled-but-landed orders.

### 3.6 pfba: Risk Engine (`backend/src/perparb/engine/risk.py`)

Delta-neutral specific risk metrics: residual delta, basis risk, liquidation distance per leg, venue concentration, correlation. Worst-of escalation: NORMAL → WARNING → DANGER → EMERGENCY → KILL_SWITCH (latches).

### 3.7 counterfactual5: CrossVenueExecutor (`scripts/execution/cross_venue_executor.py`)

Two-leg execution with rollback:

```python
# Open forward: spot_venue buy → futures_venue open_short
# On futures leg failure → sell back spot (rollback)
# Open reverse: spot_venue borrow-sell → futures_venue open_long
# On futures leg failure → buy back + repay (rollback)
# Close forward: close short → sell spot
# On spot leg failure → reopen short (rollback)
```

State tracking: `simulated | filled | rolled_back | naked | aborted`. Naked state triggers alert for manual handling. NOT post-only; uses market orders.

### 3.8 counterfactual5: PureFuturesExecutor (`scripts/execution/pure_futures_executor.py`)

Perp+perp cross-venue executor. Key: `_floor_qty()` for lot-size compliance, `_filled()` tracking, parallel leg submission via `ThreadPoolExecutor`. Position ledger in JSON (`scripts/data/pure-futures/positions.json`).

### 3.9 hummingbot: ArbitrageExecutor (`hummingbot/strategy_v2/executors/arbitrage_executor/arbitrage_executor.py`)

Fire-and-forget both legs as market orders:

```python
async def execute_arbitrage(self):
    self._status = RunnableStatus.SHUTTING_DOWN
    self.place_buy_arbitrage_order()   # MARKET buy on buying_market
    self.place_sell_arbitrage_order()  # MARKET sell on selling_market
```

Profitability check: `control_task()` polls prices from both venues, normalizes via `RateOracle`, only fires when `current_profitability > min_profitability`. No partial fill handling beyond checking both orders filled.

### 3.10 counterfactual5: Lighter Venue Adapter (`scripts/venues/lighter.py`)

Verified working against Lighter mainnet. Part of a wider venue adapter framework (Binance, Bybit, OKX, Hyperliquid, dYdX, EdgeX, Aster, Lighter). Test file: `scripts/tests/test_lighter_venue.py`.

---

## 4. Pattern Comparison: Two-Leg Entry / Exit / Rebalance

| Concern | arbTwin (wave.py) | pfba (execution.py) | counterfactual5 |
|---|---|---|---|
| **Entry leg ordering** | Lighter post-only first, OKX IOC hedge | Harder leg first (choose_lead_leg based on depth) | Parallel via ThreadPoolExecutor |
| **Passive entry** | Both legs post-only (maker mode) | Leg A passive with re-peg; leg B always crosses | N/A (market orders) |
| **Partial fill leg A** | Naked leg bounded by max_unhedged_usd + adverse stop | Size leg B down to match leg A actual fill | Rollback unfilled leg |
| **Partial fill leg B** | Wait/re-peg the other leg (EXIT_PARTIAL state) | DEGRADED → rebalance (top up or trim) | Naked state, alert, manual |
| **Rebalance trigger** | Implicit in EXIT_PARTIAL state wait | Explicit DEGRADED state + rebalance() method | settle_mismatch_planner for funding period gaps |
| **Rebalance method** | Wait + re-peg frozen price | Top up under-filled leg; fallback: trim over-filled | Rollback + reopen at correct size |
| **Exit (normal)** | Freeze price at trigger, both sides post-only, deadline → IOC | Patient: only lead passive, hedge crosses on lead fill. Timeout → cross both | Market-order both legs |
| **Exit (urgent)** | Risk stop → marketable (IOC with collar in exitqueue) | URGENT_EXITS set → crosses all | Market-order both legs |
| **Fill detection** | Poll position + price crossover | Venue acknowledges fill via adapter; poll_resting() each tick | Direct order status query |
| **Hedge ratio metric** | Implicit (qty_base equality on both legs) | `hedge_ratio() = base_exposure(long) / base_exposure(short)` | Not tracked as ratio |
| **Reconciliation** | None (trusts polling) | Three-way: DB vs venue vs memory; venue wins | JSON position ledger |

### Pattern Match to arbTwin Needs

arbTwin's `wave.py` is closest to pfba's `execution.py` in spirit: both are single-slot-per-symbol state machines with post-only preference. Key gaps in arbTwin that pfba fills:

1. **Hedge ratio as base exposure**: arbTwin assumes 1:1 quantity ratio. pfba's `base_exposure()` handles different contract specs.
2. **Rebalance as explicit state**: arbTwin's EXIT_PARTIAL wait is implicit rebalancing. pfba's DEGRADED → REBALANCING → HEDGED is explicit and auditable.
3. **Leg ordering by depth**: arbTwin hardcodes Lighter-first. pfba's `choose_lead_leg()` is venue-agnostic.
4. **Re-peg**: arbTwin's `_requote()` re-pegs every poll. pfba's `_repeg()` does the same but with a critical comment: "A limit order left where it was placed is not a quote, it is a landmine."
5. **Patient exit asymmetry**: arbTwin rests both exit legs passively. pfba only rests the lead exit leg; the hedge crosses immediately when lead closes. This bounds naked exposure on exit to zero.
6. **Reconciliation**: pfba's three-way reconciliation with "venue is the authority" rule is a gap in arbTwin.

---

## 5. Mapping to arbTwin + NautilusTrader

### What arbTwin already has that pfba doesn't

- Live execution (pfba is paper-only)
- Lighter zk-signature API key management
- OKX 400-level depth vs WS 5-level separation
- Unit calibration (calibration.py: ratio CV, return correlation)
- Issuer risk cards
- Manipulation cost profiling ("move 25bps")
- `arbtwin doctor/screen/monitor/backtest` CLI

### What NautilusTrader would bring to arbTwin

- Deterministic event-driven backtesting (arbTwin's replay doesn't simulate queue position)
- Order emulator for simulating fill probability at each price level
- OMS with position netting across symbols
- Multi-venue adapter framework (already has OKX, Bybit, Hyperliquid adapters; Lighter would need writing)
- Risk engine with portfolio-level margin
- Persistence and recovery from the event store

### Integration path

If arbTwin were ported to NautilusTrader:
1. Write a `LighterAdapter` (or `LighterExecutionClient` + `LighterDataClient`)
2. Implement `TwoLegBasisStrategy` as a `Strategy` subclass
3. Use `PositionId` + `HedgePositionId` linking for two-leg tracking
4. Use `OrderEmulator` for backtesting passive fills
5. Use NautilusTrader's `RiskEngine` for portfolio-level limits

The pfba execution patterns (leg ordering, partial-fill resizing, rebalance, patient exit) would map directly into the strategy's `on_order_book` / `on_fill` handlers.

---

## 6. Gaps and Surprises

### Surprises

1. **No production two-leg limit-order basis engine found**: pfba is paper-only; counterfactual5 uses market orders; hummingbot's ArbitrageExecutor is market-order fire-and-forget. arbTwin's `wave.py` with its freeze-price-on-trigger post-only exit is genuinely unusual in open source.

2. **pfba's "hedging leg always crosses" rule**: The most important single insight. Resting both legs is cheaper on paper but dangerous in practice because the unhedged window is unbounded.

3. **counterfactual5 has working Lighter adapter**: This is a live-tested Lighter venue integration (verify_lighter_live.py exists), which is rare in open source.

4. **NautilusTrader has no spread/arbitrage strategy examples**: Despite 31k stars and extensive documentation, no multi-venue two-leg example was found in the examples directory. The infrastructure exists but the pattern isn't demonstrated.

### Gaps in open-source coverage

1. **Top-up pricing against filled leg's average entry**: No repo implements the specific pattern of "price the second leg's top-up against the first leg's average fill price, not the other venue's mid." This is a gap arbTwin could pioneer.

2. **Lighter-specific execution patterns**: Only counterfactual5 has a Lighter adapter, and it's market-order only. No open-source repo implements Lighter's API-key-index nonce streaming, zk-signature flow, or the 300ms speed bump.

3. **Funding-period mismatch handling**: Only counterfactual5's `settle_mismatch_planner.py` addresses this. pfba's paper simulation has uniform funding periods.

4. **Post-only exit with deadline escalation**: arbTwin's `exitqueue.py` ladder (0/2/5 bps → IOC → forced marketable) is unique. pfba's exit is either patient (maker lead leg) or urgent (crosses all), with no intermediate ladder.