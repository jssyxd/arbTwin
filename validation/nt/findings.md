# T3 Findings — NautilusTrader v2.0.0rc8 + Lighter multi-key / nonce validation

Date: 2026-11-10 · Env: Windows, `validation/nt/.venv-nt2` (Python 3.14.10) · No testnet keys → dry-plan + source analysis.

## 1. Install result

| Item | Result |
|---|---|
| Requested | `nautilus_trader==2.0.0rc8` with "Lighter extra if documented" |
| Python constraint | **`>=3.13,<3.15`** — the task's "Python 3.12" premise fails: PyPI ships only cp313/cp314 wheels (win_amd64, manylinux x2, macosx arm64) + sdist. No cp312 wheel. |
| Local resolution | Local 3.12.9 unusable → used local **Python 3.14.10** (`py -3.14 -m venv --clear .venv-nt2`). Deviation from task spec, forced by upstream metadata. |
| Wheel | `nautilus_trader-2.0.0rc8-cp314-cp314-win_amd64.whl`, 68.3 MB, binary — **no Rust build** needed. `pip install` exit 0, ~8 min (SMB-share filesystem overhead; first `python -m venv` on 3.12 over SMB also timed out at 120 s and was rebuilt). |
| Lighter extra | **Does not exist.** `provides_extras = ['visualization']` only. The Lighter adapter ships inside the core wheel (`nautilus_trader.adapters.lighter` imports fine). |
| Verified | `import nautilus_trader` → `2.0.0rc8`; `LighterExecutionClientConfig`, `LighterDataClientConfig`, `LighterDataClientFactory`, `LighterExecutionClientFactory`, `LighterDeployment.{LIGHTER,ROBINHOOD}`, `LighterEnvironment.{MAINNET,TESTNET}` all importable. |

Install log: `validation/nt/install_2.0.0rc8.log`.

## 2. Adapter capabilities (source: tag v2.0.0rc8, `crates/adapters/lighter/`)

### `LighterExecutionClientConfig` fields (src/python/config.rs, src/config.rs)
`account_id` (required, default `LIGHTER-001`), `account_index: Option<u64>`, `api_key_index: Option<u8>`, `private_key: Option<SecretString>` (hex, 40-byte, 0x optional), `base_url_http`, `base_url_ws`, `proxy_url`, `environment` (`MAINNET`/`TESTNET`), `deployment` (`LIGHTER`/`ROBINHOOD`), `venue: Option<Venue>`, `http_timeout_secs=60`, `ws_timeout_secs=30`, `market_order_slippage_bps=50`, `rest_quota_per_min`, `sendtx_quota_per_min`, `transport_backend`, `use_gtd=true`, `scheduled_cancel_timeout_secs`.
`LighterDataClientConfig` adds `update_instruments_interval_mins`, `book_snapshot_timeout_secs`; its WS URL gets `readonly=true`.

Env fallbacks (Testnet): `LIGHTER_TESTNET_ACCOUNT_INDEX` / `LIGHTER_TESTNET_API_KEY_INDEX` / `LIGHTER_TESTNET_API_SECRET`. Mainnet: `LIGHTER_*`. Robinhood: `LIGHTER_ROBINHOOD[_TESTNET]_*`. **Single-key namespace only** — a two-key setup must pass per-key values in the two configs, not env.

### Nonce management (src/signing/nonce.rs)
- `NonceManager` keyed **`(account_index, api_key_index)`** — each API key has an independent nonce sequence at the venue. Lock-free: `DashMap<(i64,u8), Arc<{last_issued, baseline}>>`, CAS loop.
- Optimistic allocation: `next_nonce` issues monotonically without waiting for venue ack; **`DEFAULT_SKIP_WINDOW=16`** bounds unconfirmed allocations per key; exhaustion → `SkipWindowExhausted` error → recovery.
- **Ownership: per client.** `LighterExecutionClient::new` → `WsDispatchState::new()` → `NonceManager::default()` — each execution client owns its own manager (`src/execution.rs:381`, `src/websocket/dispatch.rs:1125-1133`).
- Lifecycle: `connect()` seeds via `GET /api/v1/nextNonce` (`refresh`, hard reset); WS reconnect → refresh + `NonceRefreshRetry` backoff loop; invalid-nonce rejection → hard refresh; skip-window exhaustion → `spawn_nonce_window_recovery` → `sync_from_venue` (monotonic lift only, never reissues signed nonces); tx ack → `ack_success` (baseline=max); rejection → `ack_failure_if_latest` (conditional rollback).
- **Venue contract** (nonce.rs doc): "With `skip_nonce=0`, the venue requires consecutive nonces per API key; callers must preserve submission order." The 16-window is an adapter capacity limit, not venue out-of-order allowance.

### Two clients, different `api_key_index`, one account — coexistence
- Each `LighterExecutionClient` holds exactly **one** `Credential` (account, key, secret). Two clients = two configs = fine: NT supports multiple exec clients per node; each client gets its own WS connection (auth token per credential), own `NonceManager`, own nonce refresh loop.
- Docs (integrations/lighter.md, "Custom venue identity"): set `venue` on both configs for distinct Nautilus identities — scopes instruments, cache entries, message topics, socket state, execution routing. `ClientId` accepts custom names. Account commands route by issuer → `account_id` issuer must match the venue (`LIGHTER_K4-001` for venue `LIGHTER_K4`).
- WS limits leave headroom: 255 connections/IP, 500 unique accounts/connection (two connections to the same account are within both).
- Both clients subscribe the same five account channels (`account_all_{orders,trades,positions,assets}`, `user_stats`) keyed by `account_index` → **both see all account activity**; order events are deduped/mapped via `client_order_index`/cloid translation tables (dispatch.rs).
- Verified locally (no network): `LiveNodeConfig(data_clients={"LIGHTER": dc}, exec_clients={"LIGHTER_K4": ea, "LIGHTER_K5": eb})` constructs successfully with both clients sharing `account_index`, different `api_key_index` (4/5), distinct per-key venues.

## 3. Multi-key verdict

**SUPPORTED** — two execution clients with different `api_key_index` on one account can coexist in one process, *at the protocol level*.

Rationale:
1. Venue nonce sequences are **per `(account_index, api_key_index)`** — key 4 and key 5 never collide by construction (upstream test `distinct_keys_track_independent_state`: key0→0, key1→100 independent).
2. Each client owns an independent `NonceManager` + nonce refresh loop; there is no shared mutable nonce state between clients.
3. NT routing supports per-client `venue`/`ClientId` scoping; account streams are shared per account but event translation is per-client.

**Caveats (why not "verified live")**:
- Per-key sequences must still be **consecutive per key** (skip_nonce=0): each client's own submission ordering is preserved internally, but *two different processes* using the SAME key would race each other's sequence (no cross-process nonce coordination exists in the adapter). One-process-per-key is NOT required; one-process-per-key-if-both-use-the-same-key is.
- The **shared account** means: `cancel_all_orders` from either client affects the whole account; scheduled cancellation is account-wide; both clients' recon sweeps see all orders (harmless: cloid translation gates reports to the originating client's cache entries).
- Live order-flow behavior (actual testnet submit/cancel, recon drift) **untested — no `LIGHTER_TESTNET_*` credentials on this box**. Verdict basis: v2.0.0rc8 source + local config-construction + nonce sandbox.

## 4. Testnet prerequisites (exact)

1. Two API keys on ONE testnet account — create at <https://testnet.app.lighter.xyz/apikeys>. Use key slot **4** and slot **5** (slots 4–254 unreserved; 0–3 reserved for Lighter clients; 255 is the `apikeys` query sentinel, not a signing key). **Save each private key at creation — Lighter never displays it again.**
2. Export (per the script's expected names):
   ```
   LIGHTER_TESTNET_ACCOUNT_INDEX   numeric account index
   LIGHTER_TESTNET_API_KEY_INDEX_4 (=4)
   LIGHTER_TESTNET_API_SECRET_4    hex private key for slot 4
   LIGHTER_TESTNET_API_KEY_INDEX_5 (=5)
   LIGHTER_TESTNET_API_SECRET_5    hex private key for slot 5
   LIGHTER_TESTNET_INSTRUMENT      e.g. 'ETH-PERP' (optional)
   ```
   NT's own env fallbacks (`LIGHTER_TESTNET_API_KEY_INDEX` / `..._API_SECRET` / `..._ACCOUNT_INDEX`) are single-key only — the script passes per-key values explicitly in config.
3. Venue reachability: `https://testnet.zklighter.elliot.ai` (reachable from this box per PLAN.zh.md). Key-sanity check without orders: `GET /api/v1/nextNonce?account_index=..&api_key_index=4` (REST weight 6).
4. Python **3.13/3.14** venv (`.venv-nt2` is 3.14.10; 3.12 will not install rc8).

Then: `validation/nt/.venv-nt2/Scripts/python.exe validation/nt/test_lighter_two_keys.py` (live mode constructs the two-client node; full submit/cancel run needs a strategy wired to both venues — see script NOTE).

## 5. Evidence index

| Claim | Source |
|---|---|
| Wheel/cp312 absence, requires_python | PyPI JSON API `pypi.org/pypi/nautilus_trader/2.0.0rc8` |
| No Lighter extra | same, `provides_extras` |
| Install success | `validation/nt/install_2.0.0rc8.log` |
| Config fields | `.src/python_config.rs`, `.src/config.rs` (tag v2.0.0rc8) |
| NonceManager per-key design, skip window 16, CAS, recovery | `.src/nonce.rs` + its test module |
| Per-client ownership | `.src/execution.rs` (client `new` → `WsDispatchState::new()`), `.src/dispatch.rs:1125` |
| Nonce refresh/sync lifecycle | `.src/execution.rs:847-896`, 2769-2847 (NonceRefreshRetry) |
| Venue custom-identity / routing | `.src/lighter_doc.md` "Custom venue identity" |
| LiveNodeConfig 2-exec-client construction | run of `test_lighter_two_keys.py` (live path, dummy keys, no network) |
| Sandbox nonce PASS (777/key, 0 dup, 0 gap) | `test_lighter_two_keys.py --dry-plan` output |
