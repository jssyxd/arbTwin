# T3 Reply — Lighter multi-key / nonce validation

**Verdict: SUPPORTED** (source-level + config-level; NOT live-verified — no testnet keys on this box).

## Deliverables
- `validation/nt/findings.md` — full evidence
- `validation/nt/test_lighter_two_keys.py` — dry-plan (runs now) + live mode (constructs two-client node)
- `validation/nt/install_2.0.0rc8.log` — install record
- `validation/nt/.venv-nt2` — NT 2.0.0rc8 venv (Python 3.14.10)

## Key results
1. **Install**: 2.0.0rc8 cp314 win_amd64 wheel (68.3 MB, no Rust build), exit 0. **Python 3.12 is unsupported** (`requires_python >=3.13,<3.15`, no cp312 wheel) — used local 3.14.10. **No "Lighter" extra exists**; adapter ships in the core wheel.
2. **Nonce design**: `NonceManager` keyed `(account_index, api_key_index)` — per-key independent venue sequences; optimistic allocation with skip-window 16; recovery via refresh (hard reset) / `sync_from_venue` (monotonic lift). Each execution client owns its own manager and nonce refresh loop; no shared mutable nonce state between clients.
3. **Two clients, one account**: verified `LiveNodeConfig` with 2 exec clients (key 4 / key 5, same `account_index`, distinct per-key `venue` + `ClientId`) constructs successfully. Account streams shared per account; events gated per-client via cloid translation.
4. **Sandbox nonce test**: 2 keys × 4 concurrent allocators, 777 issues per key — **0 duplicates, 0 gaps**, independent sequences. Mirrors upstream Rust tests.

## Caveats
- Nonces must stay consecutive **per key**; two processes sharing the SAME key would race (no cross-process nonce coordination in the adapter). One process running both keys is the supported shape.
- Account-wide effects (`cancel_all`, scheduled cancel, recon sweeps) hit both clients' view of the shared account.
- Live submit/cancel on testnet requires: account index + two key slots (4/5) + hex private keys (see findings §4 for exact env vars).
