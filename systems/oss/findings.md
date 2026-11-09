# Open-Source Systems for Two-Venue Basis Execution — Selection Dossier

> Generated: 2026-11-10 | Repo: arbTwin (OKX↔Lighter basis) | API: GitHub REST v3
> Raw data: `sources.jsonl` (29 repos, one per line JSON) | Scores: `scores.csv`

## Scoring Formula

Each candidate scored 1–5 on seven equally-weighted dimensions (max 35):

| Dimension | Metric | Thresholds |
|---|---|---|
| **Recency** | Days since last push | ≤7d=5, ≤30d=4, ≤90d=3, ≤365d=2, >365d=1 |
| **Activity** | Commits in last 90d (365d fallback) | ≥1500=5, ≥500=4, ≥100=3, ≥10=2, <10=1; null→2 |
| **Modularity** | Top-level directory count proxy | ≥20=5, ≥10=4, ≥5=3, ≥2=2, ≤1=1 |
| **Tests/CI** | GitHub Actions + test dir presence | GHA→+2, test dir→+1, test files→+1, cap 5, min 1 |
| **Docs** | docs/doc dir + examples dir | Both→5, docs+examples→4, either→3, none→1 |
| **License Fit** | SPDX license risk for proprietary integration | MIT/Apache/BSD/Unlicense=5, LGPL/MPL=3, GPL/AGPL=2, NOASSERTION/null=2 |
| **Relevance** | Directness to two-venue basis execution | Execution engines=5, crypto platforms=3-4, market data=4, backtest=4, storage=3, ops=3, general=2 |

API endpoints queried (all documented in sources.jsonl):
`GET /repos/{o}/{r}`, `GET /search/commits?q=repo:{o}/{r}+committer-date:>={d}&per_page=1`,
`GET /repos/{o}/{r}/stats/contributors`, `GET /repos/{o}/{r}/languages`,
`GET /repos/{o}/{r}/releases?per_page=1` + Link header, `GET /repos/{o}/{r}/contents/?ref={branch}`.

---

## 1. All Candidates — Scored

| # | Candidate | ★ | Pushed | c90d | Lic | R | A | M | C | D | L | V | Σ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **ccxt** | 44.8k | 2026-11-09 | 3,137 | MIT | 5 | 5 | 5 | 2 | 4 | 5 | 4 | **30** |
| 2 | **nautilus_trader** | 31.4k | 2026-11-09 | 1,943 | LGPL-3.0 | 5 | 5 | 4 | 2 | 4 | 3 | 5 | **28** |
| 3 | **kafka** | 34.2k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 5 | 3 | 4 | 5 | 3 | **27** |
| 4 | **passivbot** | 2.2k | 2026-11-09 | 1,295 | Unlicense | 5 | 4 | 4 | 3 | 3 | 5 | 3 | **27** |
| 5 | **hikyuu** | 3.7k | 2026-11-09 | 1,232 | Apache-2.0 | 5 | 4 | 3 | 2 | 3 | 5 | 4 | **26** |
| 6 | **prefect** | 24.1k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 4 | 3 | 4 | 5 | 3 | **26** |
| 7 | **vnpy** | 46.3k | 2026-11-07 | 28 | MIT | 5 | 2 | 3 | 3 | 4 | 5 | 4 | **26** |
| 8 | **dagster** | 16.4k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 4 | 2 | 4 | 5 | 3 | **25** |
| 9 | **prometheus** | 67.6k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 5 | 2 | 3 | 5 | 3 | **25** |
| 10 | **ClickHouse** | 50.8k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 4 | 3 | 3 | 5 | 2 | **24** |
| 11 | **Lean** | 22.5k | 2026-11-09 | 164 | Apache-2.0 | 5 | 3 | 5 | 2 | 2 | 5 | 2 | **24** |
| 12 | **duckdb** | 42.9k | 2026-11-09 | — | MIT | 5 | 2 | 4 | 3 | 2 | 5 | 3 | **24** |
| 13 | **freqtrade** | 56.7k | 2026-11-09 | 798 | GPL-3.0 | 5 | 4 | 4 | 3 | 3 | 2 | 3 | **24** |
| 14 | **questdb** | 17.5k | 2026-11-09 | — | Apache-2.0 | 5 | 2 | 4 | 2 | 3 | 5 | 3 | **24** |
| 15 | **hummingbot** | 21.1k | 2026-11-09 | 100 | Apache-2.0 | 5 | 2 | 3 | 3 | 1 | 5 | 4 | **23** |
| 16 | **jesse** | 8.8k | 2026-11-08 | 181 | MIT | 5 | 3 | 3 | 3 | 1 | 5 | 3 | **23** |
| 17 | **vectorbt** | 9.8k | 2026-11-08 | 72 | ? | 5 | 2 | 3 | 3 | 4 | 2 | 4 | **23** |
| 18 | **grafana** | 77.8k | 2026-11-09 | — | AGPL-3.0 | 5 | 2 | 5 | 2 | 3 | 2 | 3 | **22** |
| 19 | **cryptofeed** | 3.0k | 2026-11-09 | 80 | ? | 5 | 2 | 3 | 3 | 2 | 2 | 4 | **21** |
| 20 | **backtrader** | 23.7k | 2024-08-19 | 0 | GPL-3.0 | 1 | 1 | 3 | 3 | 1 | 2 | 4 | **15** |
| — | **WonderTrader** | — | 404 | — | — | — | — | — | — | — | — | — | **0** |

*`—` = null in API response. `c90d` nulls: GitHub search API requires `Accept: application/vnd.github.cloak-preview` for commit search on some repos (large non-Python repos); activity defaults to 2. See sources.jsonl for raw values.*

---

## 2. Top 10 Overall

### 1. **ccxt** (Σ=30) — Universal Connector
44.8k★, MIT, 3,137 commits/90d, 496 contributors. 100+ exchange unified API. If arbTwin expands beyond OKX+Lighter, ccxt is the cheapest path to multi-venue. Monorepo structure (20 top-level dirs) with per-exchange modules. Heavy dependency but battle-tested.

### 2. **nautilus_trader** (Σ=28) — The Execution Engine
31.4k★, LGPL-3.0, 1,943 commits/90d, 231 contributors. Rust-native, event-driven, HEDGING OMS, multi-venue adapters. v2.0.0rc8 has **official OKX + Lighter adapters** (Rust + PyO3). Best architectural fit for arbTwin's two-leg state machine. Only drag: LGPL-3.0 — linking OK, derivative modifications must be disclosed.

### 3. **passivbot** (Σ=27) — Lean Execution Agent
2.2k★, Unlicense, 1,295 commits/90d, 60 contributors. Single-venue grid/DCA market maker with OKX support. Unlicense means zero legal friction. Relevance limited (single-venue), but the position management and rebalance logic is clean and directly mineable.

### 4. **vnpy** (Σ=26) — Chinese Ecosystem Gateway
46.3k★, MIT, 28 commits/90d, 100 contributors. Complete Chinese market adapter suite including OKX. If arbTwin targets OKX spot + Chinese futures, vnpy's CTP adapter is production-grade. Lower commit velocity but mature/stable.

### 5. **freqtrade** (Σ=24) — Python Strategy Runner
56.7k★, GPL-3.0, 798 commits/90d, 349 contributors. Largest OSS crypto trading community. Single-exchange architecture blocks direct use for cross-venue basis, but the strategy interface, backtesting framework, and hyperopt module are the cleanest in OSS Python trading. GPL-3.0 limits code copying.

### 6. **duckdb** (Σ=24) — Embedded Analytics
42.9k★, MIT, 499 contributors. OLAP database that runs in-process. Ideal for local backtest catalogs and tick analytics — NautilusTrader's `ParquetDataCatalog` pairs naturally with DuckDB for SQL queries over parquet.

### 7. **ClickHouse** (Σ=24) — Production Time-Series
50.8k★, Apache-2.0. Columnar DB purpose-built for tick/order-book scale. If arbTwin graduates to multi-symbol tick-level storage, ClickHouse is the default choice.

### 8. **QuantConnect LEAN** (Σ=24) — Backtest + Live Engine
22.5k★, Apache-2.0, 164 commits/90d, 238 contributors. Full algo-trading engine: multi-venue, multi-asset. The `BrokerageModel` (settlement/fees) and `FillModel` (queue simulation) are directly reusable patterns. C# core with Python bindings.

### 9. **QuestDB** (Σ=24) — Tick-Native Time-Series
17.5k★, Apache-2.0. Purpose-built for financial tick data with SQL. Lower overhead than ClickHouse for single-node deployments; PostgreSQL wire protocol compatibility.

### 10. **hummingbot** (Σ=23) — Strategy Framework
21.1k★, Apache-2.0, 100 commits/90d, 237 contributors. Cross-exchange `ArbitrageExecutor`, `RateOracle` for quote conversion, `ConnectorPair` model. Market-order only (too crude for basis), but the connector framework is worth mining.

---

## 3. Best Pick Per Role

| Role | Pick | Why |
|---|---|---|
| **Execution Engine** | nautilus_trader | Official OKX+Lighter adapters, HEDGING OMS, event-sourced reconciliation. Only downside: LGPL-3.0. |
| **Market Data** | cryptofeed | Normalized WS feeds for 25+ venues including OKX. `OrderBook` with checksum/sequence tracking. License ambiguous (NOASSERTION). |
| **Storage (embedded)** | duckdb | Embedded OLAP; no server. MIT. Parquet-native; pairs with NT catalog. |
| **Storage (server)** | ClickHouse | Columnar at tick scale. Apache-2.0. |
| **Risk Engine** | pfba (`pranay123-stack/perp-funding-basis-arb`) | Delta-neutral metrics, worst-of escalation (NORMAL→WARNING→DANGER→EMERGENCY→KILL), latched kill switch. MIT. |
| **Backtest** | QuantConnect LEAN | Multi-asset, multi-venue with fill probability simulation. Apache-2.0. |
| **Ops** | prometheus + grafana | De facto standard. Prometheus Apache-2.0; Grafana AGPL-3.0 (watch hosted use). |

---

## 4. Five Components Worth Copying Into arbTwin

| # | Component | Source | Key File(s) | License | Why |
|---|---|---|---|---|---|
| 1 | **HedgeCycle state machine** | `pranay123-stack/perp-funding-basis-arb` | `backend/src/perparb/engine/hedge_cycle.py` | MIT | Two-leg lifecycle: entry→partial→hedged→degraded→exit. `base_exposure()` for venue-independent ratio. |
| 2 | **Three-way reconciliation** | `pranay123-stack/perp-funding-basis-arb` | `backend/src/perparb/engine/reconciliation.py` | MIT | DB↔venue↔memory; venue-authoritative. Mismatch classification: PHANTOM, MISSING, QUANTITY, ORPHAN. |
| 3 | **Rebalance (top-up before trim)** | `pranay123-stack/perp-funding-basis-arb` | `backend/src/perparb/engine/execution.py:699-757` | MIT | Always adds to under-filled leg before trimming. Matches SYNTHESIS §2 formula. |
| 4 | **Risk escalation ladder** | `pranay123-stack/perp-funding-basis-arb` | `backend/src/perparb/engine/risk.py` | MIT | NORMAL→WARNING→DANGER→EMERGENCY→KILL_SWITCH (latched). |
| 5 | **OKX+Lighter venue adapters** | `counterfactual5/funding-arb` | `scripts/venues/lighter.py`, `scripts/venues/okx.py` | MIT | Live-tested Lighter integration with zk-signing; direct reference for arbTwin's venue layer. |

---

## 5. Five Components NOT Worth Adopting

| # | Component | Source | Why Not |
|---|---|---|---|
| 1 | **Hummingbot ArbitrageExecutor** | `hummingbot/hummingbot` | Market-order fire-and-forget both legs. No partial-fill handling, no post-only, no rebalance. Opposite of arbTwin's execution philosophy. |
| 2 | **Freqtrade exchange layer** | `freqtrade/freqtrade` | Single-exchange architecture. Multi-venue would require rewriting the abstraction. GPL-3.0 blocks integration. |
| 3 | **Backtrader** | `mementum/backtrader` | Maintainer-archived. Last push Aug 2024. 0 commits in 90d. Single-threaded event loop; two-venue simulation a hack. |
| 4 | **zipline-reloaded** | `stefan-jansen/zipline-reloaded` | Pipeline API powerful but built for equities with daily bars. Crypto tick-level backtesting needs complete data ingestion rewrite. |
| 5 | **Redpanda/Kafka** | `redpanda-data/redpanda`, `apache/kafka` | Overkill for single-strategy two-venue system. Adds JVM/C++ dependency for problem solvable with 50-line asyncio queue. |

---

## 6. License Traps & Maintenance Risk

### License Risk Matrix

| License | Risk | Repos | Trap |
|---|---|---|---|
| **AGPL-3.0** | 🔴 HIGH | grafana | Any network use triggers copyleft. Hosted Grafana dashboards → entire stack must be AGPL-compatible. |
| **GPL-3.0** | 🔴 HIGH | freqtrade, backtrader, OctoBot | Any integration = derivative work. Cannot copy GPL code unless arbTwin becomes GPL. |
| **LGPL-3.0** | 🟡 MEDIUM | nautilus_trader | Linking is OK. Forking/modifying the core → modifications must be LGPL'd. |
| **MPL-2.0** | 🟡 LOW | tardis-machine | File-level copyleft. Modifying a file → that file stays MPL. Rest can be proprietary. |
| **NOASSERTION** | 🟡 MEDIUM | cryptofeed, vectorbt, ArcticDB, structlog | License field empty on GitHub. cryptofeed's PyPI says MIT, ArcticDB has Business Source License. **Check before copying.** |
| **null (no license)** | 🔴 HIGH | redpanda | No license = all rights reserved. Cannot use without explicit permission. |
| **MIT / Apache-2.0 / BSD / Unlicense** | 🟢 SAFE | ccxt, duckdb, ClickHouse, QuestDB, hummingbot, LEAN, passivbot, vnpy, kafka, prometheus, dagster, prefect, hikyuu, zipline-reloaded, QUANTAXIS | Standard permissive. Copy, modify, distribute. Attribute. |

### Maintenance Risk Heatmap

| Repo | Signal | Risk |
|---|---|---|
| backtrader | 0 commits/90d, last push Aug 2024, maintainer said "done" | 🔴 ABANDONED |
| zipline-reloaded | 0 commits/90d, effectively in maintenance mode | 🟡 FROZEN |
| QUANTAXIS | Last push Sep 2026, low velocity | 🟡 STALE |
| WonderTrader | 404 on GitHub — repo deleted/renamed | 🔴 GONE |
| tardis-machine | 3 contributors, 313★ | 🟡 SINGLE-MAINTAINER |
| cryptofeed | Active but license ambiguity | 🟡 LICENSE-RISK |
| vectorbt | NOASSERTION license, moderate activity | 🟡 LICENSE-RISK |

---

## Appendix: API Query Details

All queries via `GET` to `api.github.com`, authenticated with PAT (`token` scheme, not `Bearer`).
Rate limit: 5,000/hr. No rate-limit errors encountered (29 repos × ~5 calls each = ~145 calls).
See `sources.jsonl` for per-repo raw JSON responses.