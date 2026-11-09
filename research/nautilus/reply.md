# Reply: NautilusTrader Integration Blueprint

**Verdict**: NT can host this strategy. Both OKX and Lighter have official, production-grade Rust-native adapters (PyO3). No custom adapter needed.

## Key Findings

- **Version**: NT 2.0.0rc8 (2026-10-28), Python ≥3.12, active development (~300 commits/RC)
- **Both adapters exist**: `nautilus-okx` + `nautilus-lighter` — data feeds, execution, reconciliation, account streams
- **Order types match**: LIMIT post-only, IOC, GTD (OKX needs `manage_gtd_expiry`). Lighter: native OTO/OCO/OTOCO. OKX: no native contingents → strategy-managed.
- **HEDGING OMS**: keeps OKX and Lighter legs as separate positions under one strategy. NT reconciliation replaces `reconcile.py`.
- **SyntheticInstrument**: formula `LIGHTER - OKX` works for quotes. Depth gates need raw venue subs.
- **RiskEngine** handles structural checks; mechanical gates (basis band, depth, OI, liq) stay in strategy.
- **Gaps**: Lighter multi-key (one client per key [INFERENCE]), no iceberg on either venue, backtest maker fill fidelity untested for tick-level.

## Deliverables

- `research/nautilus/blueprint.md` — full 9-section blueprint
- `research/nautilus/mapping.md` — arbTwin→NT module table (53 rows)
- `research/nautilus/sources.jsonl` — 18 cited sources with URLs and findings