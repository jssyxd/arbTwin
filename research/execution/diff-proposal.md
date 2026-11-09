# Diff proposal — function-level change list (no edits applied)

Target files: `arbtwin/wave.py`, `arbtwin/engine.py`, `arbtwin/wavetrader.py`,
`arbtwin/exitqueue.py`, `arbtwin/reconcile.py`, `config/risk.yaml`, `tests/`.
Design references are to `design.md` sections. Ordered by dependency; each item is
independently reviewable. Nothing here changes the live gate
(`ARBTWIN_FILL_SOURCE=positions`, `engine._assert_live_is_safe`).

---

## A. wave.py (pure state machine — all changes keep `decide()` I/O-free)

### A1. Bug fixes required before slicing (small, independent)

| Function | Change | Why |
|---|---|---|
| `on_fill` (wave.py:420-448) | Accumulate `leg.filled_notional += qty*px`; compute `filled_px = filled_notional/filled_qty` instead of `leg.filled_px = px` (wave.py:426-428) | Current code stores the *last* fill px; with sliced fills every average (and `round_pnl_bps`) is wrong |
| `round_pnl_bps` (wave.py:451-464) | Take `unit_ratio` param; normalise Lighter prices (`p̃ = p/r`) before differencing | Raw LT px vs OKX px comparison is biased for ratio ≠ 1 (models.py:9-13 convention); REVIEW-style口径 consistency |
| `Leg` (wave.py:89-104) | Add fields: `filled_notional: float`, `state: LegState`, `slice_index: int`, `rejects: int`, `last_requote_ts: float` | Per-leg states (design §1.1) |

### A2. New pure functions (design §2)

```
class LegState(Enum)                      # design §1.1
@dataclass PairBook                       # Q_L,C_L,Q_S,C_S + combined_basis_bps(), delta(),
                                          # unhedged_usd(mark), matched_q()
def topup_limit_px(pair, missing, delta, beta, unit_ratio, missing_is_lighter) -> float
def combined_basis_with(pair, missing, delta, p_norm) -> float   # hypothetical-fill evaluator
def topup_zone(pair, delta, book_m, beta_pass, beta_cross, beta_abort,
               ladder_bps) -> (zone, px)                          # design §2.4/§2.5
def exit_limit_px(pair_exit, missing, delta, beta_exit, ...)     # mirrored §2.6
```

All unit-testable with the four worked cases in design §2.7 as fixtures.

### A3. `WaveParams` (wave.py:58-86) — new fields, defaults keep today's behaviour

```
n_slices: int = 1                 # 1 == current single-shot behaviour
slice_deadline_s: float = 600.0
b_star_pass_bps: float = 0.0      # 0 => derive: exit_bps + maker_rt + spread_cost (engine feeds)
b_star_cross_extra_bps: dict = {"okx": 3.0, "lighter": 0.0}   # + latency_cost_bps at call site
b_star_abort_bps: float = 6.5
topup_ladder_bps: float = 5.0     # design §2.4 zone-2 cap (mirrors ExitTicket.max_passive_bps)
rebalance_soft_frac: float = 0.5  # × max_unhedged_notional_usd
rebalance_cooldown_s: float = 60.0
rebalance_debounce_polls: int = 3
inventory_skew_gamma: float = 0.0 # 0 disables A-S skew (design §5.2.4)
jump_bps: float = 0.0             # 0 disables jump handling; calibrate per symbol from σ_basis
pipeline_slices: bool = False     # design §4.2 — stays off until fill-prob data says otherwise
```

### A4. `Round` (wave.py:107-157) — new fields

`slices: list[Slice]`, `slice_index: int`, `pair: PairBook`, `episode: dict | None`
(`{"kind": "complete"|"reduce", "since": ts, "actions": n}`), `markout_log: list`,
`venue_ok: dict[str,bool]`. `to_dict()` extended with slice/pair/episode for rounds.jsonl.

### A5. `decide()` (wave.py:228-417) — behavioural changes

| Branch | Change |
|---|---|
| FLAT→entry (wave.py:241-269) | Build `slices` from `n_slices`/sizing inputs (engine passes `slice_qtys: list[float]`, pre-rounded per design §4.4 — wave.py stays lot-agnostic); quote only slice 0; reset new per-round fields alongside the existing escalation/frozen reset |
| ENTRY_QUOTING (wave.py:271-286) | Re-peg only the current slice's legs; add slice-deadline check; add `venue_ok` gate → emit `Intent("cancel", …)` + set HALTED when stale |
| ENTRY_PARTIAL (wave.py:289-330) | Replace `_requote`-at-touch for the missing leg with `topup_zone()`: zone 1 → `Intent("ioc", collar=p_limit)`; zone 2 → post-only at `min(touch, p_limit)`; zone 3 → §2.5 complete-at-abort-tier vs `_abort`. Naked-usd check switches to `pair.unhedged_usd(mark)`; adverse stop unchanged; **new**: partial abort (unwind only `pair.delta()`, keep matched qty — design §5.3). On slice matched: advance `slice_index`; if slices remain → re-enter ENTRY_QUOTING for slice k+1, else HEDGED |
| HEDGED (wave.py:355-388) | Unchanged trigger; exit legs built per slice (first slice frozen px as today; later slices priced by `exit_limit_px` once a leg fills — design §2.6) |
| EXIT_QUOTING/EXIT_PARTIAL (wave.py:390-417) | Missing-leg re-price uses `exit_limit_px` off realised exit averages instead of the frozen px (frozen px remains the floor for slice 0); ladder/IOC escalation unchanged |
| new: HALTED | Return only `Intent("note", …)` + armed risk-stop intents; no quotes, no top-ups (design §1.2) |
| new: rebalance overlay (runs inside ENTRY_PARTIAL/HEDGED before zone logic) | Soft/hard thresholds, debounce, cooldown, one-way episode ratchet, optional inventory skew — design §5.2 |

### A6. `on_fill` state routing (wave.py:432-448)

Route fills to the current slice's legs; `PairBook += (q, p̃)`; slice-matched detection
(both legs `FILLED`) drives A5's advance; ABORT_UNWIND completion also clears `episode`.
Unmapped fills (no live slice for that venue/symbol) → return a sentinel so the engine can
HALT + alert (design §6 reconciliation row).

---

## B. engine.py

| Function | Change |
|---|---|
| `wave_step` (engine.py:703-752) | (1) Compute `slice_qtys` via new `_slice_plan()` (below) and pass to `wave.step`; (2) pass `venue_ok = {v: book_age(v) <= max_book_age_s}`; (3) feed `b_star_pass_bps` from `row.maker_net_bps` inputs (`row.spread_cost_bps`, `row.maker_fee_bps` — spread.py already computes both); (4) position-read staleness check: symbol in ENTRY_PARTIAL + venue position unchanged N s → critical alert (REVIEW §11.3 open item) |
| new `_slice_plan(st, notional, okx_book, lt_book)` | Design §4.1/§4.4: `S = clamp(min(S_max, S_vol, S_queue), …)`; per-slice lot rounding on the binding venue via `MarketSpec.lot_size`/`meets_minimum` (models.py:54-60); merge slices under `min_quote`; dust → last slice. Returns `list[float]` of OKX-normalised qtys |
| `_target_notional` (engine.py:675-701) | Unchanged (slice layer consumes its output) |
| `_reconcile_positions` (engine.py:947-975) | Map `FillEvent` → slice via client-order-id registry (new dict in WaveTrader, C2); unmapped → set round HALTED + critical alert instead of blind `on_fill` |
| new `_markout_recorder(symbol, venue, side, px, ts)` | Schedule +1/+10/+60 s mid reads (reuse snapshot books; no extra REST), append to rounds.jsonl; rolling-mean bench check (design §6 toxic-fill row) reusing `self.benched` |
| `run_once` (engine.py:874-911) | Kill-switch check first: flag file/env → `_cancel_stale_orders()` → reduce-only collared-IOC ladder flatten → verify flat via `PositionLedger` → write `state/kill.jsonl`, skip everything else (design §6) |
| `load_universe` (engine.py:170-227) | Wire new `WaveParams` fields from symbols.yaml/risk.yaml (same pattern as `leg_sigma_1h_bps`) |

## C. wavetrader.py

| Item | Change |
|---|---|
| `RestingQuote` (wavetrader.py:29-57) | Add `slice_index`, `client_order_id`; keep `crossed()` untouched (it is the conservative dry-run fill rule and the §7.3 tier-1 simulator) |
| `WaveTrader.quotes` keying | Currently `(symbol, venue)` — one quote per venue; keep, and enforce design §4.3 "one live order per (symbol, venue, side)" by rejecting a second quote intent for the same key with an `on_error` alert |
| `step` (wavetrader.py:124-178) | Pass through new wave.decide inputs; on `Intent("ioc")` use the collar px from the intent (already carried in `OrderRequest.px`); record slice events into `rounds.jsonl` via `_persist` |
| new client-order-id registry | `place → {client_id: (symbol, venue, slice)}` so B/`_reconcile_positions` can attribute position deltas; also the hook for REVIEW §9bis idempotent-resend (same index on retry) |

## D. exitqueue.py (reused, barely changed)

* `ExitTicket`: add optional `kind: str = "exit"` (`"rebalance_reduce"` reuses ladder +
  reduce-only + escalation semantics for design §5.3 reduce actions).
* `_best_ladder_px` unchanged — the 5 bps `topup_ladder_bps` cap in wave is deliberately
  the same shape (one concept, one implementation family).

## E. reconcile.py

* `FillEvent`: add `read_ts` (position-read freshness, REVIEW §11.3 open item).
* `PositionLedger.reconcile`: no algorithmic change; engine-side mapping only (B).

## F. config/risk.yaml + config/symbols*.yaml

```yaml
limits:
  slice_deadline_s: 600
  rebalance_cooldown_s: 60
  topup_ladder_bps: 5.0
  kill_switch_file: state/KILL
# per symbol (symbols.yaml): n_slices, pipeline_slices, jump_bps, inventory_skew_gamma
```

`max_unhedged_notional_usd: 150` semantics change from round-cap to **slice-cap**
(document in the file's comment block; round cap = 2× only if pipelining is enabled).

## G. tests (pure logic, no network — existing convention, 46 tests)

1. `topup_limit_px`: design §2.7 Cases 1–4 as exact fixtures (incl. ×unit_ratio conversion
   and the incremental Case-2 value 301.11).
2. `topup_zone`: zone boundaries at `b*_cross`, ladder cap, abort-tier fallback.
3. `PairBook`: combined basis after mixed-price fills; `on_fill` VWAP fix (A1) regression.
4. Slice lot rounding: dust merging, `min_quote` floors, last-slice remainder.
5. Rebalance ratchet: COMPLETE→REDUCE allowed once, REDUCE→COMPLETE never; debounce/cooldown.
6. HALTED: stale `venue_ok` cancels healthy venue, emits no quotes; risk stop still fires.
7. `n_slices=1` equivalence: sliced machine with one slice reproduces today's transitions
   on the MECHANICS-ROUNDS replays (migration safety).

## Explicit non-goals (this proposal)

* No market orders anywhere (operator rule; kill switch uses collared IOC ladder).
* No WS order-channel work — fills stay position-delta based (`ARBTWIN_FILL_SOURCE=positions`).
* No change to `spread.py` pricing口径 or gates; `b*` inputs are consumed, not redefined.
* Pipelining stays off by default until §7 fill-probability data exists.
