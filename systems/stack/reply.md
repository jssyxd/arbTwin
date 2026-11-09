# sys-stack reply

Three deployable stacks for the OKX↔Lighter desk, all component choices API-verified 2026-11-09/10
(stars, `pushed_at`, license):

- **A Minimal (<1 day)**: keep arbTwin custom engine; Parquet/SQLite/JSONL; Prometheus textfile
  collector + node_exporter + Alertmanager; systemd + restic + sops/age. P0 fixes first: restart
  recovery, idempotent `client_order_index`, PairBook VWAP, `abort_cost_bps`.
- **B Institutional**: NautilusTrader v2.x LiveNode (only engine with official OKX+Lighter
  adapters; LGPL), NATS JetStream, QuestDB+ClickHouse+Parquet/S3+ArcticDB, separate risk service
  and recon worker with kill authority, OTel→LGTM, canary CI/CD, OpenBao/sops, active-passive HA
  fenced by venue API-key IP whitelists.
- **C OSS-max**: cryptofeed+lighter-python ingest bridged into NT; Dagster research plane;
  Hummingbot/vn.py/LEAN/freqtrade rejected; pfba + counterfactual5 mined. Integrated-vs-replaced
  table in findings §5.4.

Verdict: buy pipes, build valves — the two-leg state machine and top-up pricing are BUILD
everywhere (no OSS implements them). Roadmap, failure modes (F1–F14), clock/latency, testnet,
secrets/nonce, reproducibility, 8-signal schema, upgrade policy: in findings.

**Deliverables**
- `systems/stack/findings.md`
- `systems/stack/components.csv`
- `systems/stack/reply.md` (this file)
