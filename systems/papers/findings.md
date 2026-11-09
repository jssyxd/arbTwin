# Code & Systems Behind the Cited Papers / Case Authors

> Research date: 2026-11-10. All GitHub numbers fetched live via GitHub API on 2026-11-10
> (commit windows: 90d = since 2026-08-12, 365d = since 2025-11-10). `null` = unavailable.
> Classification: **official** = code by the paper/case author; **reference** = third-party,
> usable implementation; **closed** = public description only, no code.

---

## 1. Author → System table

| Author / team | Paper / case | Official code? | Best usable system | Class | Activity | License |
|---|---|---|---|---|---|---|
| Timothy Leung & Xin Li | Optimal Mean Reversion Trading (arXiv:1411.5062; WS book 2016) | **No** — no GitHub for the professor (handles `timothyleung`/`leungt30`/`tlyleung` are different people; verified bios/repos) | `hudson-and-thames/arbitragelab` (implements book's OU/XOU/CIR chapters) | reference | stale (pushed 2024-05-19) | BSD-3-Clause |
| Marco Avellaneda (†2022) & Sasha Stoikov | High-frequency trading in a limit order book (2008) | **No** — Stoikov's Cornell page hosts PDFs only; GitHub handles `Avellaneda`/`Stoikov` are empty (0 repos) | `joaquinbejar/market-maker-rs` (A-S + GLFT, tested) | reference | **very active** (819 commits/90d) | MIT |
| Olivier Guéant (+Lehalle, Fernandez-Tapia) | Optimal market making (arXiv:1605.01862); GLFT (2011) | **No** — his GitHub (`OG-maths`, Paris) holds one insurance-math course repo, no MM code | `joaquinbejar/market-maker-rs` `src/strategy/glft.rs` + `tests/glft_reference.rs` | reference | very active | MIT |
| Robert Almgren & Neil Chriss | Optimal execution of portfolio transactions (2001) | **No** — pre-GitHub paper; no author code found | `AshJha0/electronic-trading` `python/src/etrade/almgren_chriss.py` | reference | active (74 commits/90d, v2.0.1) | MIT |
| Rama Cont & Alexander Kukanov | Optimal order placement in limit order markets (QF 2017) | **No** — Cont's publications page has no code links; `ramacont` GitHub = personal site repo only | `SrujayReddy/Smart-Order-Router-Backtest` (toy); `AshJha0/electronic-trading` `placement.py` (closest active) | reference (weak) | stale / active | none / MIT |
| Volker Bertram | Automated Statistical Arbitrage Strategies (Wilmott 2012) | **No** — no GitHub user; "bertram statistical arbitrage" repo search → 0 results | none worth adopting (student OU stat-arb repos ≤9★) | — | — | — |
| CBB (@Cbb0fe) | HIP-3↔IBKR equity-perps arb ($32B vol writeup) | **No** — GitHub `cbb0fe` (created 2025-09-23) has **0 repos** | closed; only X-article parameters (maxDelta 800, takerRatio 0.05/0.10, cancelDelta…) | closed | — | — |
| TierZero (tierzero.dev) | HL↔CEX funding-arb playbook | **No** — org `TierZeroDevStudio` (blog=tierzero.dev) publishes only Solana RPC/copy-trade/Polymarket-scanner tools (0★); the arb bot itself is closed; site has no GitHub links to it | closed; blog numbers only | closed | tools pushed 2026-08 | none/MIT/NOASSERTION |
| NeuralArb (neuralarb.com) | HL↔CEX cost model, funding-snipe timing | **No** — no GitHub user/org (404), no github links on site | closed; blog numbers only | closed | — | — |
| XEMM author (**djienne**) | Pacifica↔HL Rust XEMM bot (cited in x-medium findings) | **Yes — official**: full Rust source public | `djienne/XEMM_…_PACIFICA_HYPERLIQUID` + successor `djienne/CROSS_EXCHANGE_MARKET_MAKING_RUST` | official (practitioner) | active (successor: 174 commits/90d, pushed 2026-11-09) | **none (flag — all-rights-reserved)** |
| alexanderlmoran | funding-arb-bot risks.md/edges.md (cited in x-medium findings) | **Yes — official**, but 1-commit docs dump (created=pushed 2026-06-19, 48 KB) | `alexanderlmoran/funding-arb-bot` | official (snapshot) | dead | none (flag) |
| pranay123-stack | perp-funding-basis-arb (rank 1 in research/github findings) | **Yes — official** | `pranay123-stack/perp-funding-basis-arb` | official | stale (3 commits, pushed 2026-09-25) | MIT |
| counterfactual5 | funding-arb w/ Lighter+OKX adapters (rank 2 in research/github findings) | **Yes — official** | `counterfactual5/funding-arb` | official | active (pushed 2026-11-09, 106 commits/365d) | MIT |

---

## 2. Per-system notes (file:function pointers for arbTwin)

### 2.1 `joaquinbejar/market-maker-rs` — A-S / GLFT reference (MIT, ★112)
- Activity: pushed **2026-11-09**; 819 commits/90d, 902/365d; releases v0.7.0 (2026-11-09), v0.6.1 (2026-11-01); 1 contributor (bus-factor risk); size ~5 MB.
- Quality signals: 9 GitHub workflows (`tests.yml`, `code_coverage.yml`, `lint.yml`, `format_check.yml`, `semver.yml`, `build.yml`, `docs.yml`, `features.yml`, `bridge_python.yml`), `codecov.yml`, `tests/` with 30+ suites incl. `avellaneda_stoikov_fixtures.rs`, `avellaneda_stoikov_simulation.rs`, `glft_reference.rs`, `glft_forms.rs`, `instruments_basis.rs`, `hawkes*.rs`, `backtest_funding.rs`; Python bridge in `bridges/`.
- Transferable → arbTwin:
  - **MM inventory skew**: `src/strategy/avellaneda_stoikov.rs` (quote reservation price + δ± skew), `src/strategy/glft.rs` (Guéant closed forms — pairs with arxiv findings §3.4 φ=γσ²/2 identity).
  - **OU/parameter calibration**: `src/strategy/calibration.rs`, `src/strategy/walk_forward.rs` (rolling-window recalibration — addresses Huang&Wang non-stationarity concern).
  - **Risk gates**: `src/risk/circuit_breaker.rs`, `src/risk/pretrade.rs`, `src/risk/limits.rs`, `src/risk/portfolio.rs` — direct model for arbTwin's mechanics gates + `max_unhedged_notional`.
  - **Queue/L3 & basis**: `src/l3/`, `src/instruments/` (basis/calendar instruments), `tests/instruments_basis.rs`.
  - Read first: `src/strategy/avellaneda_stoikov.rs`.
- Caveat: solo maintainer; crypto-venue connectors under `src/api/`, `src/data_feeds/` are generic (no Lighter).

### 2.2 `djienne/CROSS_EXCHANGE_MARKET_MAKING_RUST` — live Lighter↔Aster↔HL bot (official XEMM successor; **no license**)
- Activity: pushed **2026-11-09**; 174 commits/90d, 232/365d; 3 contributors; created 2026-06-29; no releases; no CI workflows at root (root `tests/` = Python PnL/economics tests: `test_combined_pnl.py`, `test_economics.py`, `test_bot_stats.py`, `test_trade_history.py`).
- Layout: `LIGHTER_ASTER_BOT/src/` = `quote_engine.rs`, `inventory.rs`, `edge.rs`, `position.rs`, `vwap.rs`, `hotpath/`, `taker/`, `lighter/`, `connectors/`, `controller/`, `livebot/`, `dryrun/`, `metrics.rs`; ops docs `CALIBRATION.md`, `RUNBOOK.md`, `VPS.md`; `signers/` (Lighter zk signing), `SCREENER/` (market selection), `AGENTS.md`.
- Transferable → arbTwin (read-only; **legally not reusable — no license**):
  - **Execution state machine**: `src/controller/` + `src/livebot/` (quote→hedge→inventory loop); predecessor XEMM: `src/bot/state.rs` (states incl. hedge-queue-overflow halt), `src/strategy/opportunity.rs` (maker price derived from taker hedge price + fees + margin — the exact R2 formula from x-medium findings), `tests/fill_pipeline.rs` (4-layer fill dedup design).
  - **Inventory skew on a real perp venue**: `src/inventory.rs` + `src/quote_engine.rs`.
  - **Lighter integration**: `src/lighter/`, `signers/` — rare public Lighter order-signing code (arbTwin already has its own; useful cross-check).
  - **Dry-run methodology**: `src/dryrun/` + `LIGHTER_Market_Making/DRY_RUN.md`, `grid_dry_run.py`.
  - Read first: `LIGHTER_ASTER_BOT/RUNBOOK.md`, then `src/quote_engine.rs`.
- Sibling repos by same author: `XEMM_…_PACIFICA_HYPERLIQUID` (★21, 8 commits/90d, pushed 2026-09-30, `tests/`, `benches/`, `docs/`), `LIGHTER_Market_Making` (★36, Python, pushed 2026-07-02, has `.github/` CI, `cartea_jaimungal.py`, `vol_obi.py`, `lighter_estimators.py`, `ws_manager.py`, `tests_live/`), `AVELLANEDA_MARKET_MAKING_FREQTRADE` (★14, 22 commits/90d), `Cartea-Jaimungal_MARKET_MAKING_FREQTRADE` (★8, 189 commits/90d), `passivbot_lighter` (★11, **Unlicense** — the only djienne repo with a permissive license).

### 2.3 `AshJha0/electronic-trading` — A-C + A-S research platform (MIT, ★30)
- Activity: pushed **2026-11-06**; 74 commits/90d (repo created 2026-08-29); releases v2.0.1/v2.0.0 (2026-10-31); 2 contributors; CI: `ci.yml`, `codeql.yml`, `coverage.yml`, `docs.yml`; `.pre-commit-config.yaml`, `CHANGELOG.md`, mkdocs docs.
- Layout: `python/src/etrade/` = `almgren_chriss.py`, `avellaneda_stoikov.py`, `placement.py`, `execution.py`, `hedging.py`, `impact.py`, `tca.py`, `calibration.py`, `market.py`, `mm.py`, `analytics.py` + `python/tests/`; parallel `cpp/` (C++26), `rust/`, `java/` trees; `COOKBOOK.md`, `API_SPEC.md`.
- Transferable → arbTwin:
  - **Execution scheduling (κ, sinh trajectories)** for exit slicing / forced unwind: `python/src/etrade/almgren_chriss.py`.
  - **Limit-vs-market placement** (Cont–Kukanov-adjacent): `python/src/etrade/placement.py` [INFERENCE: covers C-K-style split per repo description "execution… placement"; not verified line-by-line].
  - **Hedging & TCA**: `hedging.py`, `tca.py` — model for `abort_cost_bps` accounting (known arbTwin gap).
  - **OU/impact calibration**: `calibration.py`, `impact.py`.
  - Read first: `python/src/etrade/almgren_chriss.py` + `COOKBOOK.md`.

### 2.4 `pranay123-stack/perp-funding-basis-arb` (MIT, ★0, paper-only)
- Activity: pushed 2026-09-25; 3 commits/90d=365d; 1 contributor; no releases; CI/tests signals: no `.github/` at root (docs in `docs/`, `PROJECT_STATUS.md`).
- Already deep-read in `research/github/findings.md` — pointers stand:
  - **Execution state machine**: `backend/src/perparb/engine/execution.py:ExecutionEngine.advance()` (LEG_A_SUBMITTED→PARTIAL→DEGRADED→HEDGED), `_repeg()` (re-peg resting quotes), `rebalance()` (top-up-before-trim), `_exit_is_patient()` (patient vs URGENT_EXITS).
  - **Pair accounting**: `backend/src/perparb/engine/hedge_cycle.py:HedgeCycle.hedge_ratio()` / `CycleLeg.base_exposure()` — venue-independent hedge ratio (contract-multiplier safe).
  - **Reconciliation (fail-closed)**: `backend/src/perparb/engine/reconciliation.py` — three-way DB↔venue↔memory, mismatch classes PHANTOM/MISSING/ORPHAN.
  - **Risk engine**: `backend/src/perparb/engine/risk.py` — worst-of escalation NORMAL→…→KILL_SWITCH (latching).
  - Read first: `backend/src/perparb/engine/execution.py`.
- Caveat: never traded live; no OKX/Lighter specifics.

### 2.5 `counterfactual5/funding-arb` (MIT, ★11)
- Activity: pushed **2026-11-09**; 1 commit/90d, 106/365d (recent push likely branch/metadata); release v0.1.0; 1 contributor; has `.github/workflows/`; `CONTRIBUTING.md`/`SECURITY.md` present.
- Layout: `scripts/` = `core/`, `execution/`, `venues/`, `strategies/`, `market/`, `backtest/`, `accounting/`, `transfer/`, `tests/`, `cli/`, `notify/`, `tools/`; `server/`, `web/`, `templates/` (dashboard).
- Transferable → arbTwin:
  - **Lighter venue adapter (live-verified)**: `scripts/venues/lighter.py` + `scripts/tests/test_lighter_venue.py` — one of very few public Lighter integrations; cross-check against arbTwin's zk-signing client.
  - **Cross-venue executor w/ rollback**: `scripts/execution/cross_venue_executor.py` (states `filled|rolled_back|naked|aborted`; naked → alert+manual) and `scripts/execution/pure_futures_executor.py` (`_floor_qty()` lot compliance, parallel legs, JSON position ledger).
  - **Funding-period mismatch**: `scripts/strategies/` settle-mismatch planner (funding window gaps — maps to arbTwin R11 OKX-8h vs Lighter-1h timing).
  - Read first: `scripts/execution/cross_venue_executor.py`.
- Caveat: market-order execution (no post-only ladder); solo maintainer.

### 2.6 `hummingbot/hummingbot` — production A-S strategy (Apache-2.0, ★21,113)
- Activity: pushed 2026-11-09; 98 commits/90d, 1,702/365d; 322 contributors; releases v2.17.0 (2026-09-22), v2.16.0 (2026-07-29).
- The v1 `avellaneda_market_making` strategy dir last touched **2026-01-26** (maintained but not hot): `hummingbot/strategy/avellaneda_market_making/avellaneda_market_making.pyx` (+`.pxd`, `avellaneda_market_making_config_map_pydantic.py`) — Guéant-Lehalle-Tapia closed forms with `inventory_risk_aversion`, order-amount auto-scaling, hanging orders; battle-tested parameter surface.
- Also relevant: `hummingbot/strategy/cross_exchange_market_making/` (XEMM pattern with taker-hedge-after-maker-fill), `hummingbot/strategy/spot_perpetual_arbitrage/`, `hummingbot/strategy_v2/executors/arbitrage_executor/arbitrage_executor.py` (market-both-legs; too crude for basis work — confirmed in research/github findings).
- Read first: `hummingbot/strategy/avellaneda_market_making/avellaneda_market_making.pyx`.

### 2.7 `nkaz001/hftbacktest` — queue-position backtesting (MIT, ★5,059)
- Activity: pushed **2025-12-23** — 0 commits/90d, 15/365d; releases rust-v0.9.4 (2025-12-10); 25 contributors. Dormant-ish but the standard for queue modelling.
- Transferable → arbTwin: **queue model / fill probability** — Rust core simulates queue position + latency; `examples/Probability Queue Models.ipynb`, `examples/Queue-Based Market Making in Large Tick Size Assets.ipynb`, `examples/GLFT Market Making Model and Grid Trading.ipynb`, `examples/Market Making with Alpha - Basis.ipynb` (basis-alpha MM!), `examples/Impact of Order Latency.ipynb`. Exactly the machinery arbTwin's dry-run lacks (move-through heuristic is optimistic per Daru findings).
- Read first: `examples/Market Making with Alpha - Basis.ipynb`.

### 2.8 `hudson-and-thames/arbitragelab` — Leung–Li chapter implementations (BSD-3-Clause, ★706)
- Activity: pushed **2024-05-19** — 0 commits/90d [INFERENCE: not re-queried; pushed date precedes window]; commercial company, repo public + BSD-3; companion `arbitrage_research` (★164, BSD-3, pushed 2024-05-02) has the notebooks.
- Transferable → arbTwin: **OU/XOU/CIR optimal entry-exit thresholds** (`arbitragelab.optimal_mean_reversion.*` — OUModel/XOUModel with minimum-profit optimization and Leung-Li band computation), half-life of mean reversion utilities (`arbitragelab.cointegration_approach.*`). Matches the arxiv findings §4 mapping (`entry_bps`→b̄, `exit_bps`→a̲, stop-loss coupling).
- Read first: docs page "Trading Under the Ornstein-Uhlenbeck Model" + `arbitragelab/optimal_mean_reversion/ou_model.py` [INFERENCE: path from docs; not file-listed via API].
- Caveat: Hudson & Thames moved newer products to paid licensing; verify the BSD-3 tree's completeness before depending on it.

### 2.9 Stale-but-cited references (lower tier)
| Repo | Why noted | Status |
|---|---|---|
| `fedecaccia/avellaneda-stoikov` ★737 | Most-starred A-S Python implementation (cited in many posts) | pushed 2023-07-06, **no license (flag)**, 1 contributor, no tests/CI |
| `mdibo/Avellaneda-Stoikov` ★154 | Classic 2017 replication | pushed 2020-05-09, no license, dead |
| `javifalces/HFTFramework` ★311 | RL-improved A-S research framework (paper-linked, `CITATION.cff`), Apache-2.0, `.github/` CI | pushed 2026-11-09, 103 commits/90d — active but research-grade (Java+Python, 425 MB) |
| `joshuapjacob/almgren-chriss-optimal-execution` ★50 | Clean A-C trajectory solver, MIT | pushed 2022-02-02, dead |
| `JJJerome/mbt_gym` ★184 | Gym envs for execution/MM RL (incl. A-C-style), BSD-3 | pushed 2024-01-08, dead |
| `kmrlab/algo-market-making` ★4 | GLFT bot w/ adaptive params, MIT | 1-day repo (2025-08-28), no tests |
| `SrujayReddy/Smart-Order-Router-Backtest` ★2 | Only found Cont-Kukanov SOR implementation (grid-search λ_over/λ_under/θ) | 1 commit 2025-04-30, no license, mocked L1 data |
| `seongmin-Jo/optimal_mean_reversion_trading` ★1 | Python implementation of arXiv 1411.5062 | pushed 2023-10-28, no license |
| `CMike007/MeanReversionTrading` ★1 | Leung-Li "with transaction costs" replication | pushed 2024-01-29, no license |

### 2.10 Closed-source systems (public descriptions only)
- **CBB** (`cbb0fe` GitHub: 0 repos) — all mechanism knowledge is in the X article: asymmetric taker thresholds, maxDelta kill-switch, cancelDelta re-quote, stale-feed post-mortem. Already distilled into x-medium findings R3/R5/R7/R8.
- **TierZero** — `TierZeroDevStudio` publishes unrelated Solana/Polymarket utilities (0★, mixed NOASSERTION licenses); the funding-arb bot is closed. Blog parameters already distilled (R11, thresholds).
- **NeuralArb** — no GitHub presence at all; blog-only cost model (child-order splitting 3–5×30–60s, clip <0.5% 24h vol → R9/R10).

---

## 3. Top 5 picks (ranked)

Ranking weights: recent activity > depth/modularity > test/CI signals > license > relevance.

1. **`joaquinbejar/market-maker-rs`** (MIT) — the only *actively released* (v0.7.0 yesterday), fully CI'd (tests+coverage+lint+semver), permissively licensed implementation of both A-S and GLFT with basis instruments and a real risk-gate layer. Adopt: `strategy/calibration.rs` patterns for OU rolling calibration; `risk/{pretrade,circuit_breaker,limits}.rs` as the template for arbTwin's mechanics gates; `avellaneda_stoikov.rs`/`glft.rs` for inventory-skewed dual_maker quoting. Rust↔Python bridge exists. Risk: solo maintainer, no Lighter connector.
2. **`AshJha0/electronic-trading`** (MIT) — newest, fast-moving platform whose module split (`almgren_chriss / avellaneda_stoikov / placement / hedging / impact / tca / calibration`) is exactly arbTwin's missing layer cake: κ-scheduled unwinds for ABORT_UNWIND, placement.py for the rest-vs-cross decision (Maglaras Θ rule), tca.py for abort-cost accounting. CodeQL + coverage CI. Risk: young repo (created 2026-08-29), API churn across v2.x.
3. **`djienne/CROSS_EXCHANGE_MARKET_MAKING_RUST`** (+ predecessor XEMM) — the most complete *live* cross-exchange perp system with Lighter integration: quote engine, inventory, taker hedge path, zk signers, dryrun harness, calibration/runbook docs, 174 commits/90d. **License: none → treat as design reference only; do not copy code.** Highest-fidelity answers to arbTwin's R1/R2/R5 rules in production form (`strategy/opportunity.rs` hedge-derived maker price; `tests/fill_pipeline.rs` 4-layer fill dedup).
4. **`counterfactual5/funding-arb`** (MIT) — the only permissively licensed repo with a **live-verified Lighter adapter** (`scripts/venues/lighter.py` + tests) plus OKX, rollback-on-failure cross-venue executor, and settle-mismatch planning for funding windows. Directly adoptable pieces: Lighter client cross-check, `naked` state semantics, lot-size flooring. Risk: market-order execution only, 1 commit/90d.
5. **`hudson-and-thames/arbitragelab`** (BSD-3) — canonical implementation of the **Leung & Li** thresholds (OU/XOU entry-exit bands, minimum-profit optimization, half-life utilities) that arbTwin's `band.py`/`wave.py` parameters map onto. Stale since 2024 but self-contained and permissive; pair with `arbitrage_research` notebooks. Honorable mention: `nkaz001/hftbacktest` (MIT) — the queue-position/latency simulator arbTwin's dry-run lacks (`Market Making with Alpha - Basis` example); ranked out of top-5 only due to 11-month inactivity.

Adoption order for arbTwin components: risk gates + calibration ← (1); unwind scheduling + TCA ← (2); quote/hedge state-machine semantics ← (3, read-only); Lighter adapter cross-check ← (4); band/threshold math ← (5).

---

## 4. Authors with NO public code (verified 2026-11-10)

- **Timothy Leung & Xin Li** — no author GitHub; book/paper code never released. (GitHub "Timothy Leung" accounts are other people.)
- **Marco Avellaneda** (d. 2022) & **Sasha Stoikov** — Stoikov's Cornell page = PDFs only; both GitHub name-handles empty.
- **Olivier Guéant** — `OG-maths` GitHub holds only a Master-2 insurance-math course repo; no MM/execution code.
- **Robert Almgren & Neil Chriss** — nothing official; paper predates GitHub norms.
- **Rama Cont & Alexander Kukanov** — Cont's Oxford/CNRS pages list papers, no code; `ramacont` GitHub = personal site; Kukanov (industry) no profile.
- **Volker Bertram** — no GitHub user, no repo implements his stat-arb paper by name.
- **CBB (@Cbb0fe)** — GitHub account exists, 0 repos; system closed.
- **TierZero** — arb bot closed; org repos are unrelated Solana/RPC tools.
- **NeuralArb** — no GitHub user/org; site links none.

## 5. License flags

- **No license (all-rights-reserved; read-only)**: all djienne main repos (XEMM, CEMMR, LIGHTER_Market_Making, AVELLANEDA/C-J freqtrade), `fedecaccia/avellaneda-stoikov`, `mdibo/Avellaneda-Stoikov`, `SrujayReddy/Smart-Order-Router-Backtest`, `alexanderlmoran/funding-arb-bot`.
- **NOASSERTION**: TierZeroDevStudio's `evm-rpc-health-check`, `polymarket-market-scanner`, `solana-tx-inspector`.
- **AGPL/LGPL in adjacent scope**: `nautilus_trader` LGPL-3.0 (already covered in research/github findings); no AGPL found among candidates here.
- Preferred-license picks: market-maker-rs (MIT), electronic-trading (MIT), funding-arb (MIT), perp-funding-basis-arb (MIT), arbitragelab (BSD-3), hummingbot/hftbacktest/HFTFramework (Apache-2.0/MIT), passivbot_lighter (Unlicense).

## 6. Method notes

- GitHub REST API with repo-scoped PAT (`.secrets/github_token`, never printed); commit counts from `commits?since=…&per_page=1` Link-header last-page; contributors likewise (`anon=true`).
- Identity verification: profile bio/blog/company cross-checks (e.g. `TierZeroDevStudio.blog == https://tierzero.dev/`); `leungt30` = chess/RL student, `timothyleung` = SWE (Burp/Django), `tlyleung` = research engineer UCL/Imperial — none is the UIC finance professor.
- Site checks: `rama.cont.perso.math.cnrs.fr/papers.html` (no code links), `people.orie.cornell.edu/sfs33/Research.htm` (PDFs only), tierzero.dev + neuralarb.com HTML grep for github.com links (none).
- Not re-verified here (taken from `research/github/findings.md`, which verified them on 2026-11-09): pfba/counterfactual5 internal file:function details.
