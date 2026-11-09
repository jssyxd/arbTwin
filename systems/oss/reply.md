# OSS Selection Dossier — Summary

29 candidates scored 1–5 on recency, activity, modularity, tests/CI, docs, license, relevance. Max 35.

**Top 3**: ccxt (Σ=30, MIT, 3,137 c/90d), nautilus_trader (Σ=28, LGPL-3.0, 1,943 c/90d), passivbot (Σ=27, Unlicense).

**Best per role**: Execution→nautilus_trader. Data→cryptofeed. Storage→duckdb/ClickHouse. Risk→pfba. Backtest→LEAN. Ops→prometheus+grafana.

**Key risks**: AGPL-3.0 (grafana) triggers hosted-use copyleft. GPL-3.0 (freqtrade/backtrader) blocks code copying. cryptofeed/vectorbt/ArcticDB have NOASSERTION license. backtrader abandoned (0 c/90d).

**5 components to copy**: HedgeCycle state machine, three-way reconciliation, top-up-before-trim rebalance, risk escalation ladder (all pfba, MIT), OKX+Lighter venue adapters (counterfactual5, MIT).

**Deliverables**: `systems/oss/findings.md` (full dossier), `systems/oss/scores.csv` (29×18), `systems/oss/sources.jsonl` (raw API), `systems/oss/reply.md`.