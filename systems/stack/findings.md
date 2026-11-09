# Reference stacks & component matrix — two-venue basis desk (OKX USDT-perp ↔ Lighter USDC-perp)

Date: 2026-11-10 · Job: `sys-stack` · Scope: `systems/stack/` only, no `arbtwin/` edits.
Companion: `components.csv` (machine-readable matrix), `reply.md` (summary).

**Verification method.** Every OSS project below was checked against the GitHub REST API on
2026-11-09/10 (stars, `pushed_at`, license SPDX, archived flag) using the repo's PAT
(`.secrets/github_token`, per `.field-guide/environment.md`). Anything not API-verified in this
session is marked `[VERIFY]`. Repo facts (engine state, NT versions, adapters, gaps) come from
`research/SYNTHESIS.zh.md` §3–§8, `research/nautilus/blueprint.md`, `research/execution/design.md`,
`research/github/findings.md`, `.field-guide/*`, `README.md` §7.

---

## 0. Verdict first

1. **Run Stack A (Minimal) now.** arbTwin's custom engine on one VPS with systemd + Parquet/SQLite +
   Prometheus textfile metrics + restic backups is a complete, honest production stack for a
   one-operator, two-venue, ≤$150-unhedged desk. It is <1 day from today's state because most of it
   already exists (`wave.py`, `reconcile.py`, `monitor.py`, `state/*.jsonl`).
2. **NautilusTrader v2.x is the only credible engine migration target** — it is the *only*
   maintained framework with **official OKX + Lighter adapters in-tree** (v2.0.0rc8, 2026-10-28;
   LGPL-3.0; 31.4k★; pushed 2026-11-09). LEAN (Apache-2.0, 22.5k★) has no Lighter → disqualified
   as a live engine here. Migrate on **2.0.0 stable**, never on an rc, and only after the shadow
   backtest gate (Roadmap P1/P2).
3. **The two-leg order state machine and top-up pricing (`PairBook`, §2 math of
   `research/execution/design.md`) is a BUILD in every stack.** No OSS project implements
   "price the top-up against the filled leg's average, not the tape" (verified across
   pfba / counterfactual5 / hummingbot / NT examples — `research/github/findings.md` §6). It is
   the desk's edge; frameworks supply everything around it.
4. **Stack B (institutional HA) is over-engineering at current capital/team size**, but is specified
   below because the task requires it; adopt it only in pieces (risk service, bus, hot TSDB) when
   the trigger conditions in §7 fire.
5. **Stack C (OSS-max) ≠ Stack B with more parts.** It is a concrete best-of-breed assembly whose
   integration seams are documented (§5.4): what is *integrated* (cryptofeed ingest, NT engine,
   QuestDB/ArcticDB storage, Dagster orchestration, LGTM observability) vs *replaced* (Hummingbot
   executors, vn.py gateways, arbTwin's hand-rolled WS/poll loops) vs *mined only* (pfba
   reconciliation patterns).

---

## 1. Constraints the stacks must serve (from repo facts)

| Constraint | Source | Consequence for stack design |
|---|---|---|
| Two venues only: OKX (USDT perp, 2/5 bps maker/taker) + Lighter (USDC perp, 0/0, 300 ms taker speed bump, zk signing, per-key nonce) | README §1.2, SYNTHESIS §0 | Adapters are the moat; prefer platforms that already have both (NT v2). Messaging/HA complexity buys little at 2 venues. |
| Live fills detected via **position deltas**, not per-order pushes (`reconcile.py`) | SYNTHESIS §1, design.md §0 | Reconciliation is load-bearing, not optional; any stack must keep a venue-authoritative recon loop with freshness timestamps. |
| Naked-leg cap 150 USD; `p_fail ≈ 0.8` measured; 4/5 rounds aborted in 2026-11-08 replay | SYNTHESIS §0.3, design.md §0 | Failure path is the common path → observability of `abort_cost_bps`, slice completion, reject rate is P0, not polish. |
| Single operator, SMB/NAS-backed dev box, Windows + VPS deployment | `.field-guide/environment.md` | Minimal stack must be file-based and systemd-managed; no k8s, no cluster DBs until P2. |
| Local NT is 1.219.0 **without Lighter**; upstream v2.0.0rc8 has official OKX+Lighter | SYNTHESIS §8, blueprint §1 | Never go live on 1.219.0 + self-written Lighter adapter (explicit SYNTHESIS recommendation); rc only for shadow/backtest. |
| Known gaps: no restart recovery from `rounds.jsonl`, no idempotent `client_order_index` resend, last-fill (not VWAP) averaging | SYNTHESIS §1 | These are P0 roadmap items in *every* stack — a framework swap does not fix them; NT's event-sourced cache fixes persistence but the strategy logic must still be written. |
| Multi-key pattern: Lighter maker key (index 4) / taker key (index 5); OKX maker/taker key split | SYNTHESIS §8, blueprint §9 | Secrets + nonce isolation is an architectural requirement: one signing process per key, never share a key across processes. |

---

## 2. Layer taxonomy (used by all three stacks and `components.csv`)

1. **ingest** — venue WS/REST capture (market data, account streams)
2. **normalize** — canonical tick/quote schema, unit normalization (`unit_ratio`), clock tagging
3. **storage** — hot TSDB, analytic store, object/Parquet lake, operational state
4. **backtest** — replay engine, fill/latency models, walk-forward
5. **execution** — venue adapters, order routing, signing
6. **order-state** — two-leg round/slice state machine, top-up pricing (the strategy core)
7. **reconciliation** — venue↔engine↔ledger three-way, orphan/unmapped-delta detection
8. **risk** — structural pre-trade checks, domain gates, kill switch, trading-state HALT
9. **observability** — metrics/logs/traces/alerts, dashboards
10. **deploy** — process supervision, containers, CI/CD, secrets, backups, upgrades
11. **orchestration** — batch/research pipelines (screening, calibration, data-quality jobs)

---

## 3. Stack A — Minimal (single VPS, <1 day to run)

**Engine decision: keep the custom arbTwin engine (SYNTHESIS §8 Path A).** It already has Lighter
zk signing, unit calibration, mechanics gates, position-delta reconciliation — the exact things a
framework migration would have to rebuild. Nautilus is added **offline-only** (backtest/replay) in P1.

### 3.1 Components per layer

| Layer | Pick (exact) | Why / integration note |
|---|---|---|
| ingest | arbTwin native clients: `venues/okx.py` (WS books5 + REST 400-level, separate caches per `.field-guide/repo-invariants.md`), `venues/lighter.py` WS; **elliottech/lighter-python** SDK (Apache-2.0, pushed 2026-11-06) for signed REST/WS | Already built; keep `quote()` vs `book()` cache separation invariant |
| normalize | in-process (`models.py`, `spread.py`: `unit_ratio`, bps convention) + **append-only JSONL** raw recordings (`monitor.jsonl` exists) | One convention per sign/denominator (SYNTHESIS §2.1 warning) |
| storage | **Parquet** (pyarrow, NT-catalog-compatible layout so P1 backtests read it directly) + **SQLite (WAL)** for rounds/ledger queries + `state/*.jsonl` for engine state | Zero ops; NT `ParquetDataCatalog` can read the same files later |
| backtest | existing `backtest/replay.py` (fill-at-next-bar, honest for signals only) + `scripts/mechanics_run.py` deterministic fake-clock replay as **CI golden gate** | Replay ≠ execution proof (SYNTHESIS §10 Kaggle finding); mechanics_run covers execution paths |
| execution | arbTwin `engine.py`/`wavetrader.py` (post-only, collared IOC, no market orders) | The operator rule "never naked market" stays in code, not config |
| order-state | `wave.py` extended per `research/execution/design.md` (PairBook VWAP, slices, zones, HALTED) | BUILD — no OSS alternative exists |
| reconciliation | `reconcile.py` position-delta ledger + startup orphan check + per-venue position-read **freshness ts** (design.md §6) | Venue is authority; missing report ≠ flat (fail-closed) |
| risk | in-process gates (`band.py`, `mechanics.py`, `fees.py`, `anchor.py`) + file-flag kill switch (`state/kill.jsonl` → cancel-all → collared-IOC ladder) | Single process = single point of failure; kill switch must survive engine panic (checked in `run_once`) |
| observability | **Prometheus** (Apache-2.0, pushed 2026-11-09) + **node_exporter** (Apache-2.0) with **textfile collector**: `monitor.py` writes `arbtwin_*.prom` every poll — no HTTP endpoint needed in the engine; **Alertmanager** → webhook/ntfy; `alerts.jsonl` kept as system of record; **Grafana** (AGPL-3.0) single binary optional | Textfile collector decouples metrics from engine liveness; add freshness alert on the `.prom` mtime (§3.3 F5) |
| deploy | **systemd** units: `arbtwin-engine`, `arbtwin-recorder`, `prometheus`, `node_exporter`, `alertmanager`; `Restart=on-failure`, `WatchdogSec`; **restic** (BSD-2 `[VERIFY] activity date`) nightly: SQLite + `state/` + Parquet catalog + sops-encrypted configs → B2/NAS, prune 7d/8w/12m; UFW + fail2ban; **chrony** NTP with `makestep` | One VPS, one operator; backup **restore drill monthly** (restic mount + sqlite open) |
| secrets | **sops + age** (MPL-2.0 / BSD-3-Clause, both active): secrets committed encrypted or kept in `.secrets/` (gitignored, current practice), injected via systemd `LoadCredential=`/`EnvironmentFile=` with 0400 perms | See §9.2 for key separation rules |
| orchestration | cron (screening rescans, calibration refresh) | Dagster is P1+, not day-1 |

### 3.2 Alternatives (and why not)

| Layer | Alternative | License / activity | Why not in Stack A |
|---|---|---|---|
| ingest | cryptofeed | AGPL-3.0, pushed 2026-11-09 | Extra process + no Lighter support `[VERIFY]`; native clients already work |
| ingest | ccxt (MIT, pushed 2026-11-09) polling | — | Polling loses the WS freshness signals; keep ccxt for one-off venue probing only |
| storage | DuckDB | MIT | Nice for ad-hoc queries over Parquet; adopt as a *query tool*, not a store |
| storage | QuestDB/ClickHouse single node | Apache-2.0 | Real ops burden (JVM/Golang services, upgrades) with no consumer at this scale |
| backtest | Nautilus BacktestNode | LGPL-3.0 | Worth it (P1) but needs the v2 upgrade + tick catalog first; not <1 day |
| engine | freqtrade (GPL-3.0), jesse (MIT), passivbot (Unlicense) | all active | Single-venue; GPL linking questions; none support Lighter |
| monitoring | Grafana Cloud / Better Stack | SaaS | Sends venue keys' activity metadata to third parties; self-host is 2 binaries |
| alerts | Telegram bot / ntfy (Apache-2.0 `[VERIFY] date`) | — | Fine as Alertmanager receiver; not a metrics system |
| backups | borgbackup (BSD/GPL dual `[VERIFY]`) | — | restic has S3/B2 native + simpler key story |

### 3.3 Failure modes (Stack A)

| # | Failure | Detection | Mitigation |
|---|---|---|---|
| F1 | Engine crash with in-memory round state; `rounds.jsonl` recovery **not implemented** (SYNTHESIS §1) → restart with unknown naked leg | systemd restart + startup orphan reconcile blocks new rounds | P0: implement startup state rebuild from `rounds.jsonl` + venue positions; until then, orphan check is the only net |
| F2 | Double-send on retry (no idempotent `client_order_index`, SYNTHESIS §1/§7) | venue duplicate-order rejects; recon unmapped deltas | P0: three-part key (venue+symbol+client_order_index) reused on resend |
| F3 | Disk full (Parquet recorder) kills SQLite writes (WAL) | node_exporter disk alerts | Separate recorder unit with `LogsDirectory` quota; rotate/ship daily; alert at 80% |
| F4 | VPS clock drift → wrong `ws_age_s`, GTD expiries, nonce rejects | `node_timex_offset_seconds` alert >50 ms; Lighter nonce errors | chrony `makestep`; never use venue timestamps as local truth (§9.1) |
| F5 | Engine hung but process alive → node_exporter still green | textfile-collector mtime staleness alert (`arbtwin_heartbeat` older than 3× poll interval) | systemd `WatchdogSec` + `sd_notify` from poll loop |
| F6 | Stale hedge-venue feed → phantom delta "corrected" into a bigger naked position (CBB −$1.1M pattern, `.field-guide/microstructure.md`) | position-read freshness ts (design.md §6); `HALTED` on book age >3 s / ws_age >30 s | Freshness vetoes are the control, not better signals |
| F7 | Backup never restored → silent corruption | monthly restore drill (restic mount, `sqlite3 PRAGMA integrity_check`, parquet schema read) | Drill is part of the runbook, logged |
| F8 | Secrets leak via env dump / shell history | `LoadCredential=` (no env), 0400 files, no secrets in unit files | sops+age; OKX keys trade-only + IP-whitelist; Lighter keys withdrawal-impossible by protocol |
| F9 | SMB/NAS workspace flake (`WinError 5` on replace, `.field-guide/environment.md`) | dev-box only; prod runs on local VPS disk | Never run the engine off the SMB share; state on VPS-local disk, restic pushes to NAS |

### 3.4 Deployment sketch

```
/etc/systemd/system/arbtwin-engine.service
  ExecStart=/opt/arbtwin/.venv/bin/python -m arbtwin run --config /opt/arbtwin/config
  Restart=on-failure  RestartSec=5  WatchdogSec=30
  LoadCredential=okx_key:/etc/arbtwin/creds/okx.key … (0400, root:arbtwin)
  ReadWritePaths=/var/lib/arbtwin/state
/opt/arbtwin/{config,state,catalog}   # git checkout + uv.lock venv, tag-pinned
/usr/local/bin/restic backup …       # nightly timer: state/ catalog/ *.db configs(sops)
```

---

## 4. Stack B — Institutional (multi-process, HA)

Trigger to build any of this: >1 operator, capital where a 30-min outage costs more than the infra,
or venues >4. Otherwise stage pieces in via §7.

### 4.1 Components per layer

| Layer | Pick (exact) | Role / topology |
|---|---|---|
| engine | **NautilusTrader v2.x** (`LiveNode`, official `nautilus-okx` + `nautilus-lighter`, LGPL-3.0) | Event-driven core; `OmsType.HEDGING`; strategy = `TwoLegBasisStrategy` (build) |
| ingest | NT adapter WS feeds (L2-signed Lighter, OKX books) → republished on bus | No separate ingest tier: adapters are the ingest |
| bus | **NATS JetStream** (Apache-2.0, pushed 2026-11-09, single Go binary) | Streams for market/account events, KV for leader election + trading state, request-reply for risk verdicts. NT external message-bus support in v2 `[VERIFY — 1.x had Redis bus]`; if absent, bridge via a thin NT `DataClient`/actor that publishes to NATS |
| storage (hot) | **QuestDB** (Apache-2.0, pushed 2026-11-09) | Ticks, order events, rounds ledger; SQL + ILP ingest `[VERIFY ILP throughput at our rates]`; Grafana native datasource |
| storage (analytics) | **ClickHouse** (Apache-2.0, pushed 2026-11-09) | LOB research, markout curves, fill-probability estimates over months of L2 |
| storage (lake) | **Parquet catalog on S3/MinIO** (MinIO AGPL-3.0 `[VERIFY license current]`, or B2) | NT `ParquetDataCatalog` ground truth; immutable, content-hashed (§9.3) |
| storage (research) | **ArcticDB** (BUSL-1.1 — free internal use, verified LICENSE.txt) | Versioned DataFrames for calibration/screening assets; not on the trading path |
| backtest | NT `BacktestNode` + custom `LatencyModel` (Lighter 300 ms bump) + custom `FillModel` (queue-reactive, design.md §7.3 tier 2) | Run as Dagster assets, not ad-hoc scripts |
| execution | NT `ExecutionClient` per venue **per key**: `LighterExecutionClient(api_key_index=4)` maker + `(=5)` taker; OKX maker/taker keys likewise | One client per key solves per-key nonce isolation (blueprint §9 critical unknown → resolved by construction, still test on testnet first) |
| order-state | `TwoLegBasisStrategy` (NT `Strategy` subclass) implementing design.md §1–§5 | BUILD; framework gives event plumbing only |
| reconciliation | (a) NT `ExecutionEngine` startup recon; (b) **separate recon worker** (own process): three-way venue↔QuestDB↔NT cache, classification PHANTOM/MISSING/QUANTITY/ORPHAN/UNEXPLAINED (pfba pattern, `research/github/findings.md` §3.5), freshness ts, unmapped delta → publishes `TRADING_STATE=HALTED` on NATS KV | Recon has **kill authority**, engine has order authority — separation of powers |
| risk service | **Separate process** subscribing to bus: unhedged-notional cap (incl. in-flight orders — Knight Capital lesson, SYNTHESIS §7), per-symbol bench, funding carry, liq distance, drift; writes NT `RiskEngine` trading state (`ACTIVE/REDUCING/HALTED`) via bus | Fail-closed: risk service down ⇒ engine sees lease expire ⇒ HALT new entries (never fail-open) |
| observability | **OpenTelemetry Python SDK** (Apache-2.0) → **Grafana LGTM**: Prometheus (metrics), Loki (logs), Tempo (traces), Grafana (AGPL-3.0, dashboards), Alertmanager (routing per README §7.3 critical/warn/info) | Schema in §9.4; round_id = trace id joins JSONL logs ↔ Tempo spans ↔ QuestDB rows |
| CI/CD | GitHub Actions + **uv** locked env + Docker (digest-pinned base) + **canary**: build → shadow node on testnet replays prod data ≥48 h → prod canary 1 symbol min-size 24 h → full; rollback = previous image tag + startup recon | Migrations expand/contract only; state schema versioned in QuestDB rows |
| secrets | **OpenBao** (MPL-2.0, Linux-Foundation Vault fork `[VERIFY]`) or **Vault** (BUSL-1.1 — fine internal) with transit/auto-unseal; or keep sops+age if team = 2 | Signing keys: dedicated **signer process** per Lighter key; app talks to signer via unix socket; key never in engine memory |
| HA | Active/passive, 2 VPS (different providers; region choice: near OKX matching infra — Tokyo/AWS ap-northeast-1 `[VERIFY hosting]`); leader lease via NATS KV; **hard fence: venue API keys IP-whitelisted to the active host's egress IP** — a zombie leader literally cannot sign | Failover runbook: lease → fence check → startup recon → resume; split-brain is impossible by whitelist, not by consensus |
| deploy | Docker Compose (bus, questdb, clickhouse, grafana stack, recon, risk, engine, signer) on the active host; k3s only if >2 hosts | Compose is enough; k8s adds a failure mode, not removes one |

### 4.2 Alternatives (and why not)

| Layer | Alternative | License / activity (verified 2026-11-09 unless noted) | Why not default |
|---|---|---|---|
| engine | QuantConnect LEAN | Apache-2.0, 22.5k★, pushed 2026-11-09 | **No Lighter adapter** → custom brokerage model incl. zk signing = the exact work SYNTHESIS §8 rejects; strong for TradFi research only |
| engine | Hummingbot | Apache-2.0, 21.1k★ | `ArbitrageExecutor` = fire-and-forget market orders both legs (findings §3.9) — violates the post-only/collar doctrine; connector code mineable only |
| bus | Redpanda (Kafka API) | license `None` via API → BSL/community license `[VERIFY]`; pushed 2026-10-08 (month-stale vs all peers) | License ambiguity + slower cadence; C++ broker is heavier to operate than NATS for our volumes |
| bus | Apache Kafka | Apache-2.0, 34.2k★ | Correct, but JVM+ZK-less-KRaft ops for ~500 msg/s is waste; pick only if team already runs it |
| hot TSDB | ClickHouse only (drop QuestDB) | Apache-2.0 | Viable simplification: one store instead of two; QuestDB earns its place only if ILP ingest + downsampling beats CH at our write pattern `[VERIFY]` |
| hot TSDB | TimescaleDB | NOASSERTION (Apache-2 + community TSL license `[VERIFY]`) | License split; Postgres extension ops |
| hot TSDB | InfluxDB 3 | Apache-2.0, pushed 2026-11-06 | Fine; weaker SQL story than QuestDB for ledger joins |
| lake | ArcticDB as primary store | BUSL-1.1 | BUSL "no Database Service" clause is fine internally, but Parquet-on-S3 is more portable for NT catalog |
| orchestration | Apache Airflow | Apache-2.0, 47.4k★ | Task-graph model fits ETL, not dataset-versioned research assets; heavier scheduler |
| orchestration | Prefect | Apache-2.0, 24.1k★ | Good, Python-native; Dagster's asset checks + partitions match walk-forward better |
| secrets | Infisical | MIT core + EE (NOASSERTION), 30.4k★ | SaaS-shaped; self-host works but younger than Vault/OpenBao |
| observability | VictoriaMetrics + Loki (drop Grafana Mimir) | Apache-2.0 (VM) | Cheaper long-term metrics; swap-in compatible via PromQL |
| observability | Sentry (error tracking) | FSL/BUSL `[VERIFY current]` | alerts.jsonl + Alertmanager already cover; Sentry adds a data processor |

### 4.3 Failure modes (Stack B)

| # | Failure | Detection | Mitigation |
|---|---|---|---|
| F1 | NATS partition between engine and risk service | risk lease TTL on NATS KV; engine heartbeat | Engine policy: lease lost ⇒ no new entries, working orders kept, exits still allowed (fail-closed on *new risk*, not on *de-risking*) |
| F2 | Failover with recon incomplete → orphan position traded over | startup recon is a hard gate; orphan ⇒ block new rounds (existing semantics) | Failover runbook automates: recon report diff to `state/`, human ack for UNEXPLAINED class |
| F3 | Split-brain dual-active | impossible to *trade* (IP-whitelist fence) but possible to *read* | Fence is venue-side, not cluster-side — the only fence that survives cloud bugs |
| F4 | ClickHouse/QuestDB down ⇒ dashboards blind but trading fine | decoupled: engine writes local WAL spool, replays to TSDB | Data plane must never gate the trading plane (drop, don't block) |
| F5 | Signer process crash → orders unsigned → naked leg can't be closed | signer health probe; engine pre-flight | Signer restart is stateless (nonce recovery via Lighter `GET /api/v1/nextNonce` — blueprint §9); keep taker-key signer on a separate process from maker-key signer |
| F6 | rc/pre-release NT upgrade breaks adapter wire format | canary gate + changelog watch (§9.5) | Pin `nautilus_trader==2.0.x`; upgrade only on stable tags; shadow replay before prod |
| F7 | Secrets store unavailable at boot | boot dependency ordering; sealed-secret fallback (sops+age decrypt to tmpfs) | Documented break-glass: age key on operator YubiKey/offline file |
| F8 | Bus latency spike corrupts cross-venue basis freshness | per-message `ts_local_recv` vs consume lag metric | Freshness veto (HALTED) is downstream of *timestamps*, not of transport health |
| F9 | OKX adapter OCO/bracket not translated (blueprint §3) — strategy assumes native contingency | contract tests against demo-trading | Use `OrderEmulator` or strategy-managed sibling cancel; never rely on un-emulated OCO on OKX |
| F10 | Cost creep: 9 services on 2 hosts, one operator | quarterly infra review | Stack B is opt-in per component (§7); each must name the failure it prevents |

---

## 5. Stack C — OSS-max (best-of-breed assembly)

Philosophy: take the strongest maintained OSS per layer, accept **two integration seams you must
build** (ingest→NT catalog writer; two-leg strategy), replace nothing with vapor.

### 5.1 Assembly

| Layer | Pick | License / activity (verified 2026-11-09) |
|---|---|---|
| ingest (market) | **cryptofeed** (OKX L2/trades/funding/OI, normalized callbacks) + **elliottech/lighter-python** for Lighter WS/REST (cryptofeed Lighter support `[VERIFY — likely absent]`) | cryptofeed: AGPL-3.0 (verified LICENSE text), 3.0k★, pushed 2026-11-09; lighter-python: Apache-2.0, pushed 2026-11-06 |
| ingest (bridge) | **BUILD** ~300 LOC `cryptofeed→NT Parquet catalog` writer + NATS publisher (AGPL is fine: internal use, no network service offered) | the only unavoidable glue |
| normalize | NT v2 data types (`QuoteTick`/`TradeTick`/`OrderBookDelta`) as canonical schema; `unit_ratio` normalization stays in strategy/domain code | schema discipline from blueprint §6 (synthetic prices vs bps) |
| storage | **QuestDB** (hot) + **ArcticDB** (research frames, BUSL-1.1 verified) + **Parquet on S3/B2** (lake) + **ClickHouse** (LOB analytics) | all pushed within 48 h of verification |
| backtest | **NT BacktestNode** + custom `LatencyModel` (300 ms bump) + queue-reactive `FillModel`; **Dagster** (Apache-2.0, 16.4k★) partitions = walk-forward windows, asset checks = data-quality gates | replaces `replay.py` for execution-fidelity work; `replay.py` kept for signal honesty |
| execution | **NT LiveNode** official OKX + Lighter adapters; **ccxt** (MIT, 44.8k★) *read-only* for venue screening/probing (no Lighter in ccxt `[VERIFY]`; never on the order path) | vn.py (MIT, 46.3k★) **not integrated** — its OKX gateway duplicates NT's, and it has no Lighter |
| order-state | **BUILD** `TwoLegBasisStrategy` (PairBook, slices, zones, HALTED) | same build as Stack B; OSS-max changes infrastructure, not the edge |
| reconciliation | NT startup recon + **BUILD** three-way worker mining **pfba** patterns (MIT, paper-only, `research/github/findings.md` §3.5) and **counterfactual5/funding-arb** (MIT, live-tested Lighter adapter, pushed 2026-11-09) as reference implementations | Hummingbot executors **replaced** (market-order fire-and-forget, findings §3.9) |
| risk | NT `RiskEngine` (structural: precision, notional, rate limits, trading state) + **BUILD** domain gates (basis band, mechanics, naked cap incl. in-flight, markout/toxic-fill bench) | blueprint §5 lists exactly which gates stay custom |
| observability | OTel SDK → Prometheus + Loki + Tempo + Grafana; **ntfy** (Apache-2.0 `[VERIFY date]`) for push; alerts.jsonl retained as audit trail | LGTM stack AGPL — internal use fine |
| orchestration | **Dagster** for screening/calibration/backtest/data-quality assets; live trading **never** touches Dagster | trading plane and research plane separated by process, not convention |
| deploy | Docker Compose → (optional) k3s; systemd for the engine if container latency is a concern | digest-pinned images, uv.lock in image |

### 5.2 Alternatives per layer
Identical to §4.2 for shared layers; OSS-max-specific:

| Layer | Alternative | Why not |
|---|---|---|
| ingest | NT adapters as the only ingest (Stack B style) | Fewer moving parts; cryptofeed earns its place only if you want funding/OI/multi-exchange screening feeds NT doesn't subscribe |
| ingest | ccxt.pro WS | MIT + unified, but per-exchange depth control weaker than cryptofeed; no Lighter `[VERIFY]` |
| storage | DuckDB-over-Parquet instead of ClickHouse | Zero-ops, great for research notebooks; no concurrent writer for the live ledger |
| orchestration | Prefect / Airflow | see §4.2 |
| framework mining | jesse (MIT), passivbot (Unlicense) | single-venue; nothing to take that pfba doesn't already give |

### 5.3 Failure modes
Union of Stack B F1–F10 plus:

| # | Failure | Detection | Mitigation |
|---|---|---|---|
| F11 | AGPL/BUSL license drift (cryptofeed AGPL, ArcticDB BUSL, Grafana AGPL) | license CI scan (`pip-licenses` / ScanCode) pinned in lockfile review | Internal-use-only rule documented; re-scan on every dependency bump; no offering of these as services |
| F12 | cryptofeed↔NT schema skew after either upgrades (tick precision, book delta semantics) | bridge contract tests replay a golden recording day, byte-comparing catalog output | Golden-day fixture in CI; both projects pin exact versions |
| F13 | Integration surface = 6 projects × upgrade cadence | changelog watch (§9.5) covers all six | Upgrade one project per month max; bus/schema changes get shadow-node week |
| F14 | "Best-of-breed" becomes nobody-owned: glue code is the actual system | LOC census: glue+strategy must stay <20% of repo | If glue grows, collapse toward Stack B (NT does more) — the stacks are a spectrum |

### 5.4 Integrated vs replaced vs mined (exact)

| Project | Disposition |
|---|---|
| NautilusTrader v2 | **Integrated** (engine, adapters, recon, risk core, backtest, catalog). Replaces: `engine.py` poll loop, `reconcile.py` transport, `backtest/replay.py` for execution-fidelity runs, hand-rolled WS clients |
| cryptofeed | **Integrated** (market/funding/OI ingest for OKX + screening venues). Replaces: nothing live on day 1 — runs beside native clients until the bridge is golden-tested |
| elliottech/lighter-python | **Integrated** (Lighter signed REST/WS where NT adapter gaps appear, e.g. `nextNonce` recovery tooling). Does not replace NT's Lighter client |
| ccxt | **Integrated read-only** (screening, venue probes, cross-checks). Replaced on order path by NT adapters — ccxt never signs an order |
| QuestDB / ClickHouse / ArcticDB / Parquet-S3 | **Integrated** (hot / analytics / research / lake). Replace: JSONL-as-database, SQLite at scale, ad-hoc CSV research files |
| Dagster | **Integrated** (research/batch plane). Replaces: cron sprawl, manual walk-forward scripts |
| Grafana LGTM + OTel + ntfy | **Integrated**. Replace: eyeballing `monitor.jsonl`, webhook-only alerts |
| Hummingbot | **Replaced / mined**: `ArbitrageExecutor` pattern rejected (market orders); `RateOracle` quote-conversion pattern mined if a third quote currency appears |
| vn.py | **Not integrated**: OKX gateway redundant, no Lighter; revisit only for CTP/TradFi venues |
| pfba (pranay123-stack) | **Mined** (paper-only): three-way recon taxonomy, `base_exposure()` hedge ratio, DEGRADED→rebalance states, "hedging leg always crosses" rule |
| counterfactual5/funding-arb | **Mined**: live-tested Lighter adapter as cross-check for NT's Lighter client; `settle_mismatch_planner` for 1h-vs-8h funding period mismatch |
| freqtrade / jesse / passivbot | **Not integrated**: single-venue |
| LEAN | **Not integrated** as engine (no Lighter); usable later for TradFi leg research only |

---

## 6. Master component matrix

Full machine-readable matrix: **`systems/stack/components.csv`**
(`layer,component,role,license,activity,fit,stack`). Activity values are API-verified
`pushed_at` dates from this session unless marked `[VERIFY]`. Stack codes: `A`=Minimal,
`B`=Institutional, `C`=OSS-max; `alt`=evaluated alternative, not picked.

Summary counts: 3 stacks × 11 layers; 21 projects **integrated** across stacks, 9 evaluated as
alternatives, 6 explicitly rejected/not-integrated (LEAN-as-engine, Hummingbot-executors, vn.py,
freqtrade, jesse, passivbot), 2 mined-only (pfba, counterfactual5).

---

## 7. P0/P1/P2 adoption roadmap for arbTwin

**P0 — this week, custom engine stays live (Stack A + gap fixes). No framework migration.**
1. Restart recovery: rebuild round/slice state from `rounds.jsonl` + venue positions at boot (F1).
2. Idempotent resend: reuse `client_order_index` (three-part key) on every retry (F2, SYNTHESIS §7).
3. `on_fill` → PairBook notional accumulation (`C+=q·p`, `P̄=C/Q`) + `round_pnl_bps` `/unit_ratio` fix (SYNTHESIS §1 known gaps).
4. `abort_cost_bps` field + slice events into `rounds.jsonl` (design.md §7.1 metrics).
5. Metrics: `monitor.py` writes textfile-collector `.prom` for the 8 signals (§9.4 names) + heartbeat; Prometheus+node_exporter+Alertmanager units; alert on `.prom` staleness.
6. systemd units + WatchdogSec; restic nightly + first restore drill; chrony.
7. sops+age encryption of `.secrets/` content; systemd `LoadCredential=`; OKX keys IP-whitelisted, trade-only.
8. `uv.lock` pin everything; CI = tests + `mechanics_run.py` golden replay.

**P1 — weeks 2–4, offline NT + data foundation (Stack C pieces, zero live risk).**
9. Upgrade research env to NT **v2.0.0rc8 or later stable** (never live on rc; local 1.219.0 stays for the custom engine's imports).
10. Tick recorder: OKX+Lighter L2/trades → NT `ParquetDataCatalog` layout on VPS-local disk (cryptofeed bridge if it beats extending native recorders).
11. NT `BacktestNode` replay of recorded days with custom `LatencyModel` (300 ms) + conservative FillModel; measure `capture_fraction`, `p_fill` per level, time-to-pair (design.md §7).
12. Dagster (or structured cron → Dagster later) for screening recalibration, unit-ratio re-check, walk-forward partitions.
13. Grafana + Loki dashboards over QuestDB-lite (start: Prometheus + JSONL tail; add QuestDB when query pain is real).
14. Testnet harness (§9.1): OKX demo-trading + Lighter testnet keys, same image, config-only diff; shadow `TwoLegBasisStrategy` dry-run.
15. Upgrade policy live (§9.5): changelog-watch cron on nautilus_trader / lighter-python / cryptofeed releases.

**P2 — months 2–3, execution migration + separation of powers (Stack B/C core).**
16. Port order-state to NT `TwoLegBasisStrategy` (blueprint §8 P0–P2 plan), gated by: ≥2 weeks shadow-node parity vs custom engine on identical feeds, mechanics_run suite green, testnet completion rate ≥40% / net ≥2 bps / abort ≤0.5× gross (SYNTHESIS §11 acceptance).
17. Live cutover behind feature flag, one symbol, min size; custom engine kept warm as rollback (both cannot hold keys to the same account simultaneously — cutover moves keys).
18. Separate risk service + recon worker with HALT authority on NATS; engine consumes trading state.
19. QuestDB hot store + ClickHouse analytics; ArcticDB research frames; Parquet lake content-hashed.
20. HA active/passive only if F1 (single-VPS outage) has actually cost money — else stop here.

**Explicit non-goals**: k8s, Kafka, Vault cluster, multi-region active-active — none names a failure
this desk has had.

---

## 8. Build vs buy per layer

| Layer | Verdict | Rationale |
|---|---|---|
| ingest | **BUY/ADOPT** (native SDKs, cryptofeed, NT adapters) | Commodity; venue wire formats change monthly; community absorbs churn |
| normalize | **ADOPT** NT schema + small build (unit_ratio domain code) | Schema is standard; the USDT/USDC + unit_ratio convention is domain knowledge |
| storage | **ADOPT** (Parquet/SQLite → QuestDB/ClickHouse/ArcticDB/S3) | Zero differentiation; all picks are hour-scale to deploy |
| backtest | **HYBRID**: adopt NT engine, **build** FillModel/LatencyModel | Engine is commodity; queue-reactive maker fills + speed bump are desk-specific and decide sizing truth |
| execution/adapters | **ADOPT** NT official OKX+Lighter | Writing a zk-signing Lighter adapter was explicitly rejected (SYNTHESIS §8); LGPL used as library = fine |
| order-state | **BUILD** | The only layer where OSS has *nothing* (findings §6 gaps); PairBook top-up pricing is the edge |
| reconciliation | **HYBRID**: adopt NT startup recon, **build** three-way worker | NT covers engine↔venue; ledger↔venue↔engine with freshness/unmapped-delta semantics (CBB lesson) is yours |
| risk | **HYBRID**: adopt NT RiskEngine, **build** domain gates + kill authority | Structural checks commodity; in-flight-inclusive exposure, funding-mismatch, toxic-fill bench are not (blueprint §5) |
| observability | **ADOPT** (Prometheus/LGTM/OTel) + build schema | Tools commodity; the 8-signal semantics and alert tiers are domain (§9.4) |
| deploy/secrets | **ADOPT** (systemd/Docker/sops/age/OpenBao/restic) | Never build secrets or supervision |
| orchestration | **ADOPT** Dagster | Research-plane commodity; keep it off the trading plane |

Rule of thumb: **buy pipes, build valves.** Every layer that moves bytes is adopted; every layer that
decides *whether to send an order* is built.

---

## 9. Cross-cutting designs

### 9.1 Multi-venue clock/latency handling + testnet/sandbox

**Clocks.**
- Single time discipline: chrony on every host, `makestep 1.0 3`; alert `node_timex_offset_seconds` >50 ms. All logs/metrics in UTC epoch ns.
- **Never treat venue timestamps as comparable across venues.** Record three clocks per event: `ts_event` (venue), `ts_recv` (local wall, ns), `ts_mono` (monotonic). Basis decisions use local receipt ordering; venue ts is for audit only. NT already models `ts_event`/`ts_init` — keep the convention in JSONL too.
- Measure per-venue one-way skew periodically: OKX `GET /api/v5/public/time`, Lighter block/time endpoint `[VERIFY endpoint]`; store `offset ± rtt/2` as a *tagged uncertainty*, never silently correct feeds (corrected feeds hide staleness — the CBB failure was trusting a stale-but-plausible read).
- Freshness vetoes are the real latency control: `max_book_age_s=3`, `ws_age_s>30` ⇒ `HALTED` (design.md §1.2/§6). Lighter's 300 ms taker speed bump is priced into `b*_cross(B)=b*_pass+σ·√(300ms)` (design.md §2.3), and into backtests via NT custom `LatencyModel` — a fill model without the bump systematically overstates hedge-leg performance.
- Latency budget table per symbol (record, don't assume): WS hop, decision, sign (Lighter zk signing cost is per-key and serial — README §4.4), venue ack. Alert when p95 budget exceeds the slice deadline share.

**Testnet/sandbox strategy.**
- OKX **demo trading** (separate demo API keys; `x-simulated-trading: 1`) — full order lifecycle incl. post-only rejects; Lighter **testnet** (separate keys/index `[VERIFY URL]`).
- NT `SandboxExecutionClient` (present in local 1.219.0 adapter list per `.field-guide/environment.md`) for state-machine unit tests without any venue.
- Same artifact everywhere: one Docker image / one venv tag; prod vs testnet differ **only** in credentials + config file (which is itself git-versioned, §9.3).
- Deterministic mechanism replay (`scripts/mechanics_run.py`, fake clock) is the CI gate; testnet is the integration gate; prod min-size is the calibration gate. Promotion requires all three green (design.md §7.4 tier logic).
- Testnet fills are *not* evidence of maker fill probability (no real queue) — testnet validates mechanics (signing, nonces, recon, error paths); `capture_fraction` comes only from prod small-size orders (design.md §7.3 tier 3).

### 9.2 Secrets / keys separation

| Secret | Where it lives | Rules |
|---|---|---|
| OKX API key/secret/**passphrase** (maker key; taker key) | sops+age file (A) / OpenBao (B); injected via systemd `LoadCredential=` — never env in unit files, never shell history | Trade-only permission, **withdraw disabled**, **IP-whitelisted to active host egress**; passphrase stored separately from key+secret (different age recipient file) so one leak ≠ signing capability; maker/taker keys are separate credentials with separate rate-limit pools |
| Lighter zk private key + `api_key_index` (index 4 maker, index 5 taker) | Dedicated **signer process** per key (B) or 0400 cred file (A); key never crosses a process boundary in plaintext | **One key = one process = one nonce stream.** Nonce windows are per-key (blueprint §9); two clients sharing a key corrupt nonces → order rejects at best, replay at worst. NT: one `LighterExecutionClient` per `api_key_index` — do not attempt multi-key in one client. Nonce recovery: `GET /api/v1/nextNonce` on reconnect (blueprint) |
| Infra secrets (webhook, S3, DB, Grafana) | Same store, separate age keys / Vault namespace | Webhook URLs are secrets (they can fake alerts); rotate with the rest |
| Backup encryption key (restic) | **Offline** (operator password manager / YubiKey), never on the VPS | A VPS compromise must not yield readable backups of signing keys |

Rotation: quarterly, plus on any host compromise; rehearse rotation on testnet keys first (a
rotation that has never been practiced is an outage). Audit: log *usage* (signer emits
`key_index` + op count metric, never the key), review monthly against expected order counts
(`reject_rate`/order-rate anomalies can indicate key misuse).

### 9.3 Reproducibility

- **Data versioning**: recordings are immutable. NT Parquet catalog per capture session; each session gets a `manifest.json` {session_id, start/end, symbols, per-file SHA-256, recorder git tag, config hash}. No in-place edits — corrections are new sessions with a `supersedes` link. External datasets (Kaggle funding/OI, binance-full-history — SYNTHESIS §10) pinned by dataset version tag, recorded in the manifest.
- **Config versioning**: every config file git-committed; runtime stamps `git_rev + sha256(config/*.yaml)` into every `rounds.jsonl` record, every backtest result row, every alert. A PnL number without a config hash is not reproducible and must not inform sizing. NT configs = Python config classes serialized to yaml in-repo (blueprint §7).
- **Deterministic replay**: (a) `mechanics_run.py` fake-clock + seeded RNG golden replay in CI (5 rounds of 2026-11-08 as fixture — SYNTHESIS §11.2 already replays them); (b) NT `BacktestNode` replay from catalog with fixed seeds, `carry_replay_events_on_reopen` noted (blueprint §1); (c) decision code must take time from an injected clock — enforce with a CI grep/lint ban on `time.time()`/`datetime.now()` outside adapters; (d) golden-output diff on the 8-signal monitor row for a recorded day.
- **Environment**: `uv.lock` (+ `--require-hashes` for wheels), container image digests recorded per run; Python 3.12 pinned (NT ≥3.12 requirement, blueprint §1).

### 9.4 Observability

**The 8 signals (README §7.1) as Prometheus metrics** (textfile collector in A, OTel/exporter in B/C):

| README signal | Metric | Type / labels | Alert (README §7.3 tier) |
|---|---|---|---|
| `ws_age_s` >30 | `arbtwin_ws_age_seconds{venue}` | gauge | warn >15, critical >30 → HALTED |
| `basis_z` \|z\|>4 | `arbtwin_basis_z{symbol}` | gauge | info |
| `net_bps` <0 held | `arbtwin_net_bps{symbol}` | gauge | warn (held & <0 for >5 min) |
| `unhedged_usd` / single-leg timeout | `arbtwin_unhedged_usd{symbol}`, `arbtwin_single_leg_age_seconds{symbol}` | gauge | critical > cap(150) or > `max_unhedged_ms` |
| `liq_distance_pct` <18 | `arbtwin_liq_distance_pct{venue,symbol}` | gauge | critical <18 |
| `funding_carry_apr` >25% | `arbtwin_funding_carry_apr{symbol}` | gauge | warn (units: OKX per-8h decimal vs Lighter per-hour pct — normalize before export, `.field-guide/repo-invariants.md` 100–240× trap) |
| `oi_utilization` >25% | `arbtwin_oi_utilization{venue,symbol}` | gauge | warn |
| `reject_rate` >20%/50 | `arbtwin_reject_rate{venue}` | gauge (rolling 50) | critical >0.2 |

Plus platform: `arbtwin_heartbeat_ts_seconds` (staleness alert), `arbtwin_round_state{symbol}`
(state gauge: flat/entry/partial/hedged/exit/abort/halted), `arbtwin_abort_cost_bps` histogram,
`arbtwin_slice_completion_ratio`, `arbtwin_position_read_age_seconds{venue}` (freshness veto),
`arbtwin_order_latency_seconds{venue,phase}` histogram, node_exporter host metrics.

**Log schema** (JSONL, all streams): `{ts, ts_mono, level, scope, venue?, symbol?, round_id?, slice_id?, client_order_index?, event, msg, …fields}`; `event` from a closed vocabulary
(`decision, order_submit, order_ack, order_reject, fill_delta, recon_result, gate_veto, halt, resume, alert`). Logs never contain keys/passphrases/nonces.

**Trace schema** (OTel, B/C): one trace per round, `trace_id ↔ round_id`; spans:
`entry_decision` → `slice[k].leg[venue].order` → `fill_confirm` → `rebalance_episode` → `exit_slice[k]` → `recon`; span attributes carry `b_comb`, `zone` (CROSS/REST/NO_TOPUP), `p_limit`, `abort_cost_bps`. Metric/log/trace join keys: `round_id`, `slice_id`, `client_order_index`.

Cardinality rule: labels are bounded (venue=2, symbol≤ screened set ~200, state=8); never label by
order id or price.

### 9.5 Upgrade policy

- **Pin**: exact versions in `uv.lock`; NT pinned to stable tags only (currently local 1.219.0 for the custom engine; v2.0.0rc8 allowed **only** in the offline shadow/backtest env until 2.0.0 stable — SYNTHESIS §8); container bases digest-pinned; no `latest` anywhere.
- **Changelog watch**: cron job hitting GitHub Releases API (same pattern used in this research) for `nautilus_trader`, `elliottech/lighter-python`, `cryptofeed`, `questdb`, `nats-server`, plus OKX API-update page and Lighter apidocs changelog `[VERIFY URLs]`; diff summary → warn channel. Venue API changes have bitten the desk before (`reject_rate` is literally "venue changed rules" — README §7.1).
- **Canary**: dependency bump PR → CI (tests + mechanics golden replay) → shadow node replays yesterday's prod feed on testnet ≥48 h → prod canary (1 symbol, min size, 24 h) → full. Adapter/engine upgrades always take the shadow week; pure-infra upgrades (Grafana, restic) skip it.
- **Rollback**: previous image tag / venv kept on-host (`/opt/arbtwin/releases/<tag>` symlink flip); rollback is safe *because* startup reconciliation is venue-authoritative — the rolled-back engine re-derives state from positions + `rounds.jsonl`, never from assumptions. DB/schema changes expand-then-contract so old code reads new data.
- **Cadence cap**: one engine/adapter upgrade per month; security fixes exempt. Every upgrade lands with a CHANGELOG.md entry in-repo (the repo already maintains one).

---

## 10. Sources & verification log

- GitHub REST API, 2026-11-09/10 (PAT auth, `token` scheme): stars/`pushed_at`/license/archived for all 30 repos listed in `components.csv`. Notable: nautilus_trader LGPL-3.0, 31,405★, pushed 2026-11-09; Lean Apache-2.0, pushed 2026-11-09; ArcticDB LICENSE.txt = **BUSL-1.1** (verbatim fetch); cryptofeed LICENSE = **AGPL-3.0** (verbatim fetch); Redpanda license endpoint 404, repo pushed 2026-10-08 → `[VERIFY]`.
- Repo: `research/SYNTHESIS.zh.md` §0–§11; `research/nautilus/blueprint.md` §1–§9; `research/execution/design.md` §0–§8; `research/github/findings.md` §1–§6; `.field-guide/{environment,repo-invariants,microstructure}.md`; `README.md` §6–§7; `systems/README.md`.
- Not verified this session (marked inline): Lighter testnet URL + time endpoint, ccxt/cryptofeed/Hummingbot Lighter support, NT v2 external message-bus status, OKX/Lighter hosting regions, ntfy/restic/OpenBao exact activity dates, QuestDB ILP throughput, MinIO/Vault/Sentry current license texts.
