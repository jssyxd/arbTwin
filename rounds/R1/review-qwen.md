# R1 independent review — qwen3.8-max (2026-11-10)

Inputs: `rounds/R1/{README.md, screen_rwa.log, symbols.rwa.yaml, round_check.json,
rwa_live.log, alerts.jsonl}`, `config/risk.rwa.yaml`, `docs/ROUNDS-RWA-2026-11.md` §0–4.
No repo code opened. `[INFERENCE]` marks anything not literally in those files.

**Bottom line: the live artifacts do not say what the README says they say.**
`rwa_live.log` / `alerts.jsonl` contain exactly six lines: five identical sizing vetoes
and one flat-position check. There is no basis-vs-entry WAIT line, no `dry_run` field,
no config echo, no order line. The README's "no trade yet because basis 0.4–5.1 bps <
entry 6.26, so the engine is correctly waiting" is **not supported** by the live log;
the live log shows the engine never reached a basis decision — it died at the sizing
gate every time.

---

## 0. Evidence integrity (must be fixed before any per-item verdict means anything)

| # | Fact in artifact | README/doc claim | Status |
|---|---|---|---|
| E1 | `rwa_live.log`: `sizing $12.00 below the round floor $25.00 (equity okx=10000.00/lighter=10000.00)` ×5 | "no trade yet: CRCL basis 0.4–5.1 bps vs entry 6.26 … correctly waiting" | **Contradicted.** Root cause is a stale `min_round_notional_usd=25`, not economics. README blocker 3 admits the config was changed to 10 and the process not restarted — the log proves the running process is *still* the old one. |
| E2 | same line: `equity okx=10000.00/lighter=10000.00` | "equity: OKX 31.479 USD, Lighter 27.629 USDC" | **Contradicted.** The live process reads a stub 10000.00 on both venues. Every equity-derived limit (`max_margin_usage_pct: 0.35`, sizing) is being evaluated against ~320× the real balance. `[INFERENCE]` 10000.00 is a paper/default fallback, consistent with docs §1 "OKX 私有 API 缺 `OKX_API_PASSPHRASE`；未通过私有鉴权，live 下单 BLOCKED". |
| E3 | `alerts.jsonl` ordering: five warns at ts 1794248744.4 → 1794248816.7, then `"startup position check: flat"` at ts 1794248838.1 | "STARTUP position check: flat" | **Anomalous.** A *startup* check cannot postdate five evaluation cycles by 94 s. Either two runs are concatenated, the file is not append-ordered, or "startup" is a periodic reconcile mislabelled. The evidence file itself is not trustworthy as a timeline. |
| E4 | `round_check.json` window ts 1794248391.5 → 1794248565.5 (174.0 s); first live warn ts 1794248744.4 | data check + live arming presented as one round | **Disjoint.** The data check ends 178.9 s before the first live warn and 272.6 s before the flat check. The PASS says nothing about the process that is actually armed. |
| E5 | `round_check.json`: `records 15`, `basis_p95 6.67`, `actions WAIT 14 / MAKER 1` | "CRCL 14 rows … basis p95 6.80 … WAIT 13 / MAKER 1" | **Three numeric mismatches.** The README was written against a different (earlier) run than the committed JSON. |
| E6 | docs §1 "live 下单 BLOCKED", §7 blocker 1 "OKX_API_PASSPHRASE 必需" | "`dry_run:false` in the latest snapshot" | **Irreconcilable as presented.** No artifact contains `dry_run:false`; the claim has no citation. |
| E7 | live warn cadence 11.4 / 12.5 / 30.7 / 17.7 s; snapshot cadence ~12.4 s mean | docs §5 `arbtwin run --interval 1` (1 s) | **Contradicted.** Observed cadence is ~20× the configured interval. Either the live unit does not pass `--interval 1`, or it cannot keep up. Unexplained. |

---

## 1. Universe — CRCL-only: **FIX** (verdict right, reasoning and thresholds wrong)

**KORU was NOT wrongly rejected. Rejection is correct and decisive, not borderline.**

- 14 d (`screen_rwa.log`): `ratio cv 0.554% / return corr 0.9807`. The README cites
  **0.507%** for this rejection; 0.507% is the *30 d* number. The README attributes a
  30 d statistic to the 14 d decision — misquote.
- KORU fails **both** bounds, and the corr failure (0.9807 vs ≥0.99) is independent of
  the CV bound, so relaxing CV to 0.56% would still reject it. "Borderline" is wrong.
- `[INFERENCE]` KORU is a 3× daily-reset leveraged single-country ETF. Its ratio is
  non-stationary *by construction* (daily rebalance + volatility decay), which is exactly
  what a 10× higher ratio CV (0.554% vs CRCL 0.045%) plus sub-0.99 return correlation
  looks like. A longer window drifts worse, not better — the 30 d re-screen is consistent
  with that. Structurally "not one instrument" is the correct call, and it is the *reason*
  the gate exists. Docs §2 had pre-listed KORU as a first-round candidate; the screen
  correctly overrode the prior. **KEEP the rejection.**

Fixes required on the screen itself:

- **F1 — the CV gate does almost no work; the corr gate short-circuits the economics.**
  Eight symbols sit under the 0.500% CV bound (XAU 0.025, SPY 0.0259, NVDA 0.0353,
  AAPL 0.0354, TSLA 0.0407, QQQ 0.0454, MRVL 0.0468, TSM 0.0545). US100 (corr 0.9875),
  IBM (0.9839), US500 (0.9353), BMNR, KORU are all rejected with `net_taker_bps: 0.0` —
  i.e. never costed. A single arbitrary 0.99 correlation threshold on 14 d of 1-minute
  data is vetoing symbols whose net bps was never computed. Compute net bps for
  corr-failures too, so the recorded rejection reason is economic.
- **F2 — the rejection message is malformed.** `ratio cv 0.554% / return corr 0.9807
  outside [0.500%, 0.99]` fuses two different inequalities (cv ≤ 0.500%, corr ≥ 0.99)
  into one interval. This formatting bug is what made a decisive rejection readable as
  "borderline" in the README. Report each bound separately with its own pass/fail.
- **F3 — n=4.** CRCL `events: 4`, `completion: 1.0`. A p95 over four observations is the
  sample max; `entry_bps_p95: 6.26` and `basis_p95_rth_bps: 6.26` carry no statistical
  content. Require n ≥ 30 events or publish a bootstrap CI on `net_per_round_bps`.
- **F4 — CRCL was approved at the same thinness that rejected AAPL and NVDA.**
  `trades_per_day: 0.28` is verbatim the number AAPL/NVDA were rejected for
  ("thin: 0.28 trades/day"), and `fill_probability: 0.035`. CRCL survived only because
  its net cleared +1.0 bps. The thinness gate is therefore not applied consistently — it
  is applied *after* the net gate, so a thin symbol with a marginal net is admitted. At
  0.28 trades/day and 3.5% fill probability, R1 cannot reach its own pass criterion
  ("双边成交") on any realistic timescale; docs §3 R1 requires a bilateral fill.
- **F5 — CRCL fails the round's own documented entry rule.** Docs §4: entry requires
  `|basis| ≥ p95` **and** `maker_net ≥ 2 bps`. `symbols.rwa.yaml` records
  `net_per_round_bps: 1.25` < 2. `[INFERENCE]` `net_per_round_bps` may be a taker-side
  screen metric distinct from `maker_net`, but no artifact shows `maker_net ≥ 2` for
  CRCL. As screened, CRCL does not clear the policy it is being traded under.
- **F6 — `band_override_bps: 6.26` sits below `config/risk.rwa.yaml` → `bands.floor_bps: 8.0`.**
  The symbol overrides a global floor to a value under that floor. If `floor_bps` is a
  veto, CRCL should never have armed; if it is advisory, the file's own header
  ("Everything in `limits` is a veto, not a suggestion") does not cover `bands`, and that
  needs to be stated.
- **F7 — `net_per_round_bps: 1.25` is not reproducible from the printed components.**
  `capture 10.59 − b* 8.12 = 2.47`; `basis_p95 8.96 − b* 8.12 = 0.84`;
  `basis_p95_rth 6.26 − b* 8.12 = **−1.86**`. Three plausible definitions, none gives
  1.25, and the RTH one — the only session the live engine trades
  (`round_check.json → sessions.rth`, n=15 of 15) — is **negative**. `[INFERENCE]` on
  which formula the screener used; the fact that the headline +1.25 is derived from the
  24 h p95 while the armed path uses the RTH p95 of 6.26 against an all-in breakeven
  `b* = 8.12` is arithmetic, not inference. **Entry below breakeven is structurally
  negative EV.** This is the single strongest argument for HOLD and it is independent of
  every execution bug above.

## 2. Data check — PASS is **not valid**: **FIX / re-run**

- **The gate is looser than the phenomenon it is meant to catch.** `max_gap_s: 30.0`
  against an observed cadence of ~12–21 s means a *single missed cycle* (2× cadence)
  still passes. The bound cannot detect the failure mode it exists for. The README calls
  this deliberate; a check that cannot fail is not a check.
- **The window is 174.0 s / 15 records.** That is a smoke test, not a data check. It
  covers no session boundary, no open/close auction, no reconnect, no funding timestamp,
  no weekend gap. Docs §3 R1 demands "14 天 1m + 资金费 + 两所价差" for the data-check
  step; the committed JSON validates 2.9 minutes of snapshots.
- **The R1 pass criterion "数据缺口 <0.1%" is unmeasured.** `round_check.json` has no
  gap-fraction or expected-vs-actual-record-count field. Against docs §5's configured
  `--interval 1`, the committed window should hold ~174 records over its own 174.0 s span;
  it holds 15 (~91% missing, mean inter-record 12.4 s). `"fails": []` cannot tell that
  apart from a clean tape, because nothing counts expected records. Against docs §3's R1
  data-check spec (14 d of 1 m = 20160 rows) the shortfall is total.
- **`max_book_age_s: 3.0` and `max_quote_ttl_s: 120` are unenforced.** `stale_s: 9.25`
  (3.1× the 3.0 s policy) with `fails: []`. `[INFERENCE]` `stale_s` may be "age of the
  newest record at check time" rather than book age; either way no check ties book age to
  the armed policy.
- **A 20 s cadence makes the 800 ms hedge budget and the 3 s book-age limit unenforceable
  in principle.** `max_unhedged_ms: 800` and `max_book_age_s: 3.0` are both far below one
  observation interval. If the decision loop truly runs at the snapshot cadence, the
  engine cannot observe a naked leg within its own budget. The PASS therefore certifies
  nothing about the two limits that matter most for leg risk. This must be resolved by
  instrumenting decision-loop latency separately from snapshot-write cadence — the two
  are currently conflated, and E7 shows the configured interval is not being honoured.

## 3. Risk parameters — **dollar risk is genuinely small; the guarantees are invalid**: **FIX**

Solvency first, because it bounds everything else. Total equity 31.479 + 27.629 = 59.108.

| Exposure | 1σ | 3σ | Source |
|---|---|---|---|
| Naked leg, 800 ms (as configured) | $0.0016 | $0.005 | σ₁ₕ 87.9 bps → 1.465 bps/√s |
| Naked leg, 20 s (actual cadence) | $0.008 | $0.024 | same |
| Naked leg, **unhedgeable**, full 2 h hold | $0.149 | $0.45 | σ₂ₕ = 87.9·√2 = 124.3 bps |
| `emergency_stop_loss_pct: 0.03` on $12 | — | $0.36 | config |

Worst plausible single-round loss ≈ $0.45 = 0.76% of equity. **The size is safe.** No
parameter here can meaningfully hurt a $59 account. The problems are structural:

- **R1 — the stop is 240× the edge.** `emergency_stop_loss_pct: 0.03` = 300 bps =
  2.41σ of a 2 h hold (σ₂ₕ 124.3 bps), so it fires on ordinary noise a few percent of
  the time. One firing costs −300 bps against a screened edge of +1.25 bps/round →
  **240 rounds to recover**, which at 0.28 trades/day is ~857 days. The expected value of
  the strategy is dominated by stop-out frequency, and that frequency was never estimated.
  A 3% stop is sized for a trend strategy, not a 1.25 bps mean-reversion.
- **R2 — `max_unhedged_notional_usd: 15.0` > `max_notional_usd: 12.0`.** The naked cap can
  never bind; a single max-size leg is always within it. Combined with E2/E6 (OKX private
  auth documented as BLOCKED), the real tail is not an 800 ms hedge window but a
  **permanently unhedgeable** Lighter leg — the −$0.45 / 3σ row above. That is the
  scenario the config does not model, and no artifact shows an unwind path when the hedge
  venue is unreachable. Docs §4 promises "90 min 未配对 → 被动 unwind"; passive unwind
  requires the very OKX private auth that docs §1 says is missing.
- **R3 — two documented kill switches are absent from `config/risk.rwa.yaml`.** Docs §4:
  "连续 2 轮净亏或单日回撤 >5% → 停手" and "逆向 25 bps → 退出队列". The config has
  neither a consecutive-loss nor a daily-drawdown parameter, and its exit is 300 bps, not
  the documented 25 bps — 12× looser. `[INFERENCE]` both rules are unimplemented, so the
  strategy has no automated stop-trading. With R1's asymmetry that is the missing
  protection that actually matters.
- **R4 — `min_round_notional_usd` units are inconsistent.** The yaml comment says "per
  symbol across both legs"; the live line compares a per-leg number to it
  (`sizing $12.00 below the round floor $25.00`), so the old 25 was per-leg, i.e. $50/round.
  Under the new value 10, a $12/leg round ($24 total) passes with 2.4× headroom. Whichever
  unit is intended, comment and enforcement disagree, and the disagreement is what
  silently vetoed every live cycle.
- **R5 — no minimum-leverage constraint.** `max_margin_usage_pct: 0.35` on Lighter's
  27.629 USDC caps margin at $9.67, so the documented $12/leg is only reachable at
  ≥1.24× on the smaller venue. Docs §4 states ≤3× but nothing states a floor; at 1× the
  config self-vetoes. `[INFERENCE]` benign at the intended 3× ($4 margin = 14.5% of
  Lighter, 12.7% of OKX), but it is an unstated dependency.
- **R6 — `registry: {}` is empty.** `config/risk.rwa.yaml` defines hard/soft structural
  flags (`synthetic_spot`, `thin_float`, `recently_listed`, `no_index_price`,
  `issuer_is_counterparty`, …) and records them "as a human judgement … so it can be
  reviewed". CRCL — a tokenized equity perp on two venues, the exact case the flag list
  was written for — has no entry. The human-judgement step for the only approved symbol
  was skipped.
- **R7 — minimum notional and minimum fee at $10–12 are unverified, and they dominate.**
  `b* = 8.12 bps` assumes `fees 4.0 bps` → $0.0048 per leg on $12. `[INFERENCE]` any real
  venue minimum ticket fee (typically $0.01–$0.10) makes the true cost 8–80 bps/leg,
  which turns +1.25 bps/round into a large negative. The whole edge is smaller than one
  plausible minimum-fee step. No artifact records either venue's min size or min fee.

## 4. Live evidence — `dry_run:false` + flat + no trade is **not** proof of arming: **FAIL**

No. Three separate reasons, all from the artifacts:

1. **`dry_run:false` appears in no artifact.** It is asserted in the README §3 with no
   citation. The files I was given contain no config echo, no startup banner, no
   `dry_run` field. Docs §1 states the opposite condition ("live 下单 BLOCKED",
   missing `OKX_API_PASSPHRASE`) and docs §5 describes the service as
   `ARBTWIN_DRY_RUN=1`. Claiming arming on this evidence violates docs §0's own
   constitution: "Prove It Works … 不接受'看起来对'".
2. **Flat + no trade is consistent with a broken engine.** Absence of fills cannot
   distinguish "correctly waiting on basis" from "cannot sign", "wrong permissions",
   "order rejected", "sizing vetoed". Here it is the last one: all five live lines are
   sizing vetoes, so the engine never evaluated basis at all. The README's economic
   explanation is a post-hoc story the log does not contain. Worse, the *direction* of
   the evidence is inverted — a live-armed engine that cannot read real equity (E2) and
   cannot authenticate to its hedge venue (docs §1) is more dangerous than a dry run, not
   less.
3. **The flat check is self-undermining.** If OKX private auth is missing, an OKX position
   query cannot return "flat" — it errors. So either the check silently covers Lighter
   only and reports it as "both venues", or docs §1/§7 are stale, or the position read is
   also a stub like the 10000.00 equity. `[INFERENCE]` All three readings mean "flat" is
   not evidence of two-venue private auth. E3 (startup line logged after five cycles)
   independently damages this file's credibility.

**The one line that proves anything, and how far it goes:**

> `{"ts": 1794248838.079701, "level": "info", "scope": "engine", "message": "startup position check: flat (no open positions)"}`

That is the strongest line in the evidence set: it proves a process ran, reached a
position-query step, and completed it without error. It does **not** prove `dry_run:false`,
does not prove OKX private auth, does not prove order-placement capability, and does not
prove the running config matches the reviewed `config/risk.rwa.yaml` — E1 proves the
opposite on that last point. Nothing in the set proves a single byte reached a venue.

## 5. Blockers — the single most important missing check

> **Prove OKX private authentication and real equity in one observable line: a signed OKX
> private call whose response is echoed into the live log showing the real balance
> (31.479), plus a startup line echoing the loaded `limits` block.**

Why this one over the others:

- It is the only blocker that converts a *dangerous* state into a safe one. Today the
  engine is claimed armed (`dry_run:false`) while docs §1 says the hedge leg's private API
  is unauthenticated. That combination means any Lighter fill becomes a leg that **cannot
  be hedged or passively unwound** — R2's permanent-naked tail, with docs §4's 90-minute
  unwind remedy unavailable. Un-arming (or proving auth) removes the tail entirely.
- It explains E2 without further investigation: `equity okx=10000.00` is what an
  unauthenticated or stubbed balance read looks like, and it silently disables
  `max_margin_usage_pct: 0.35` and sizing. Every other risk limit is downstream of a
  real equity number.
- It is cheap and reversible: one signed request, one log line. Docs §0 "Never Block on
  the Human" does not apply — docs §7 lists the passphrase as a hard credential blocker,
  so this is the one item that legitimately requires the human, and it has been open since
  before R1 started.
- Satisfied by exactly this, and nothing weaker: a log line containing a venue-returned
  balance ≈31.479 **and** an OKX order-id or client-order-id from a place+cancel at the
  intended $10–12 size. The place+cancel additionally discharges R7 (min notional, min
  fee) and E1 (floor semantics) in the same action, which is why it is the highest-
  information single check available.

Runner-up, not a substitute: re-run `round_check.py` over ≥24 h spanning a session
boundary with `max_gap_s` ≤ 2× measured cadence, a gap-fraction field against the <0.1%
criterion, and decision-loop latency instrumented separately from snapshot cadence.

---

## Summary

| Item | Verdict |
|---|---|
| Universe / CRCL-only | **FIX** — KORU correctly rejected (README misquotes 0.507% for the 14 d 0.554%, and corr 0.9807 fails independently); screen gates F1–F7, incl. RTH net −1.86 bps vs headline +1.25 |
| Data check PASS | **FIX / re-run** — 174 s window, `max_gap_s=30` cannot fail at 20 s cadence, <0.1% gap criterion unmeasured, `max_book_age_s` unenforced |
| Risk config | **FIX** — dollar risk ≤$0.45/round is safe; 300 bps stop vs 1.25 bps edge is not; naked cap non-binding; 2 documented kill switches unconfigured; `registry: {}`; min fee/notional unverified |
| Live arming evidence | **FAIL** — `dry_run:false` uncited and contradicted by docs §1/§5; live log shows stale-config sizing vetoes and a stub equity of 10000.00; no venue round-trip |
| R1 vs its own pass criteria (docs §3: 双边成交 / 无裸腿超时 / 实际净额 ≥ 0 / 缺口 <0.1%) | **0 of 4 demonstrated** |

VERDICT: HOLD

HOLD because the live artifacts show an engine running a stale config against a stub
10000.00 equity with its hedge venue documented as unauthenticated — so the RTH entry
threshold of 6.26 bps sits below the 8.12 bps breakeven and `dry_run:false` is an
uncited claim, not evidence, and no order has ever touched a venue.
