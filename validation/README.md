# Validation workspace

Objectives and environment/data plan: [`PLAN.zh.md`](PLAN.zh.md).

| Dir | Owner job | Deliverable |
|---|---|---|
| `harness/` | val-harness | deterministic replay, fill-model interface, metrics, self-test |
| `ou/` | val-ou | OU half-life/threshold calibration (reachable data) |
| `fill/` | val-fill | Lighter recorder, queue-reactive fill model, markout/p_fail |
| `venue/` | val-venue | OKX amend / clOrdId idempotency test kit (demo) |
| `nt/` | val-nt2 | NautilusTrader v2 + Lighter multi-key/nonce test |
| `reports/` | all | run summaries and captain synthesis |

Environment facts (2026-11-10): Lighter mainnet/testnet reachable; OKX/Binance/Bybit
unreachable from this host (DNS 169.254.0.2); no trading credentials in repo.
See `PLAN.zh.md` §5 for the exact VPS/proxy and demo/testnet credentials required.