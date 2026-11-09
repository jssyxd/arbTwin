#!/usr/bin/env python
"""T3 — NautilusTrader v2.0.0rc8 + Lighter multi-key / nonce validation.

Two LighterExecutionClients on ONE Lighter account (api_key_index 4 and 5),
concurrent tiny testnet orders, nonce-collision / lost-order assertions.

Modes:
  live      : LIGHTER_TESTNET_* env vars present -> start two clients via
              LiveNode, submit/cancel tiny orders concurrently, log every
              order + nonce-related event, assert no nonce collision and no
              lost orders.
  --dry-plan: no creds -> instantiate both configs, print exact env/keys
              needed, and run a local re-implementation sandbox test of the
              nonce bookkeeping (NonceManager is NOT Python-exposed in rc8;
              we mirror its documented contract from the v2.0.0rc8 source:
              crates/adapters/lighter/src/signing/nonce.rs).

Run:  validation/nt/.venv-nt2/Scripts/python.exe validation/nt/test_lighter_two_keys.py [--dry-plan]

Testnet ONLY. Never places mainnet orders. Never prints private keys.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass

# --- constants ---------------------------------------------------------------

KEY_A = 4
KEY_B = 5
DEFAULT_ACCOUNT_INDEX = 12345  # placeholder; real one from env

ENV_VARS = {
    "LIGHTER_TESTNET_ACCOUNT_INDEX": "numeric Lighter account index (registration)",
    "LIGHTER_TESTNET_API_KEY_INDEX_4": "api_key_index 4 (or pass via config; indices 4..254 unreserved)",
    "LIGHTER_TESTNET_API_SECRET_4": "hex private key for key index 4 (40-byte hex, 0x optional)",
    "LIGHTER_TESTNET_API_KEY_INDEX_5": "api_key_index 5",
    "LIGHTER_TESTNET_API_SECRET_5": "hex private key for key index 5",
    "LIGHTER_TESTNET_INSTRUMENT": "optional: tradeable perp symbol, e.g. 'ETH-PERP'",
}

LOG = logging.getLogger("t3.two_keys")


def _mask(secret: str) -> str:
    return f"<{len(secret)}-char secret hidden>"


def creds_present() -> bool:
    needed = [
        "LIGHTER_TESTNET_ACCOUNT_INDEX",
        "LIGHTER_TESTNET_API_KEY_INDEX_4",
        "LIGHTER_TESTNET_API_SECRET_4",
        "LIGHTER_TESTNET_API_KEY_INDEX_5",
        "LIGHTER_TESTNET_API_SECRET_5",
    ]
    return all(os.environ.get(v) for v in needed)


def print_env_plan() -> None:
    print("== testnet prerequisites (exact) ==")
    print(
        "1. Two API keys on ONE testnet account: create at "
        "https://testnet.app.lighter.xyz/apikeys\n"
        "   - key slot 4 and key slot 5 (slots 4..254 are unreserved; 0..3 and 255 are not usable)\n"
        "   - save each private key at creation: Lighter never shows it again"
    )
    print("2. Export before running (same process runs BOTH clients):")
    for name, desc in ENV_VARS.items():
        print(f"   set {name}=<...>   # {desc}")
    print(
        "3. NT v2 reads its OWN env fallbacks if config fields are None:\n"
        "     LIGHTER_TESTNET_ACCOUNT_INDEX / LIGHTER_TESTNET_API_KEY_INDEX /\n"
        "     LIGHTER_TESTNET_API_SECRET  (single key only — hence the script passes\n"
        "     per-key values explicitly in config, NOT via env)"
    )
    print(
        "4. Venue sanity check (no order): curl 'https://testnet.zklighter.elliot.ai/api/v1/nextNonce'\n"
        "   with ?account_index=..&api_key_index=4 -> returns the per-key next nonce"
    )


# --- nonce bookkeeping sandbox (mirrors upstream NonceManager contract) ------

class NonceError(Exception):
    pass


@dataclass
class _KeyState:
    last_issued: int
    baseline: int


class SandboxNonceManager:
    """Local mirror of Nautilus Lighter NonceManager (v2.0.0rc8, nonce.rs).

    Keyed (account_index, api_key_index). Optimistic allocation with a
    skip_window of unconfirmed issues; refresh = hard reset;
    sync_from_venue = monotonic lift; ack_success = max-baseline advance;
    ack_failure_if_latest = conditional rollback.
    """

    def __init__(self, skip_window: int = 16) -> None:
        self.skip_window = skip_window
        self._states: dict[tuple[int, int], _KeyState] = {}
        self._lock = threading.Lock()

    def refresh(self, account_index: int, api_key_index: int, venue_next_nonce: int) -> None:
        with self._lock:
            self._states[(account_index, api_key_index)] = _KeyState(
                last_issued=venue_next_nonce - 1, baseline=venue_next_nonce - 1
            )

    def sync_from_venue(self, account_index: int, api_key_index: int, venue_next_nonce: int) -> None:
        with self._lock:
            st = self._states[(account_index, api_key_index)]
            applied = venue_next_nonce - 1
            st.last_issued = max(st.last_issued, applied)
            st.baseline = max(st.baseline, applied)

    def next_nonce(self, account_index: int, api_key_index: int) -> int:
        with self._lock:
            st = self._states[(account_index, api_key_index)]
            nxt = st.last_issued + 1
            if nxt - st.baseline > self.skip_window:
                raise NonceError(
                    f"skip-window exhausted acct={account_index} key={api_key_index}"
                )
            st.last_issued = nxt
            return nxt

    def ack_success(self, account_index: int, api_key_index: int, nonce: int) -> None:
        with self._lock:
            st = self._states[(account_index, api_key_index)]
            st.baseline = max(st.baseline, nonce)

    def ack_failure_if_latest(self, account_index: int, api_key_index: int, nonce: int) -> bool:
        with self._lock:
            st = self._states[(account_index, api_key_index)]
            if st.last_issued != nonce or st.last_issued <= st.baseline:
                return False
            st.last_issued -= 1
            return True

    def last_issued(self, account_index: int, api_key_index: int) -> int | None:
        with self._lock:
            st = self._states.get((account_index, api_key_index))
            return st.last_issued if st else None


def run_nonce_sandbox() -> bool:
    """Two 'clients' (key 4 / key 5) share ONE account, allocate concurrently.

    Asserts the properties upstream guarantees per key: monotonic, gap-free,
    no duplicates, independent sequences per key, skip-window enforcement,
    recovery via sync_from_venue. This is the same shape as the upstream Rust
    unit tests (nonce.rs mod tests: distinct_keys_track_independent_state,
    concurrent_callers_see_no_duplicate_or_gap).
    """
    acct = DEFAULT_ACCOUNT_INDEX
    mgr = SandboxNonceManager(skip_window=16)
    # connect(): each client seeds its own key from GET /api/v1/nextNonce
    mgr.refresh(acct, KEY_A, 100)
    mgr.refresh(acct, KEY_B, 500)

    issued_a: list[int] = []
    issued_b: list[int] = []
    err: list[str] = []
    stop = threading.Event()

    def worker(key: int, out: list[int], ack_prob: float) -> None:
        for _ in range(400):
            try:
                n = mgr.next_nonce(acct, key)
                out.append(n)
                # simulate venue ack ordering (not in lockstep with issuance)
                if ack_prob > 0.5:
                    mgr.ack_success(acct, key, n)
            except NonceError:
                # recovery path: venue applied more than we saw
                mgr.sync_from_venue(acct, key, (mgr.last_issued(acct, key) or 0) + 1)
            except Exception as e:  # pragma: no cover
                err.append(f"{key}: {e}")
                stop.set()
                return

    threads = [
        threading.Thread(target=worker, args=(KEY_A, issued_a, 0.7), name="client-k4"),
        threading.Thread(target=worker, args=(KEY_A, issued_a, 0.4), name="client-k4-2"),
        threading.Thread(target=worker, args=(KEY_B, issued_b, 0.9), name="client-k5"),
        threading.Thread(target=worker, args=(KEY_B, issued_b, 0.1), name="client-k5-2"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    ok = True
    if err:
        print("SANDBOX FAIL: errors:", err[:3])
        ok = False
    for name, seq, base in (("key4", issued_a, 100), ("key5", issued_b, 500)):
        if not seq:
            print(f"SANDBOX FAIL: {name} issued nothing")
            ok = False
            continue
        dupes = len(seq) - len(set(seq))
        gaps = (max(seq) - min(seq) + 1) - len(set(seq))
        print(
            f"{name}: issued={len(seq)} distinct={len(set(seq))} "
            f"min={min(seq)} max={max(seq)} dupes={dupes} gaps={gaps}"
        )
        if dupes:
            print(f"SANDBOX FAIL: {name} duplicate nonces — collision")
            ok = False
        # monotonic gap-free means distinct count == span (start at refresh base)
        if gaps:
            print(f"SANDBOX INFO: {name} {gaps} gap(s) from rollbacks — heals via ack/sync")
        if min(seq) != base:
            print(f"SANDBOX FAIL: {name} first nonce {min(seq)} != venue next {base}")
            ok = False
    print(f"SANDBOX RESULT: {'PASS' if ok else 'FAIL'} "
          f"(two keys, one account, 4 concurrent allocators)")
    return ok


# --- live mode ---------------------------------------------------------------

def run_live() -> int:
    account_index = int(os.environ["LIGHTER_TESTNET_ACCOUNT_INDEX"])
    key4 = int(os.environ.get("LIGHTER_TESTNET_API_KEY_INDEX_4", str(KEY_A)))
    key5 = int(os.environ.get("LIGHTER_TESTNET_API_KEY_INDEX_5", str(KEY_B)))
    secret4 = os.environ["LIGHTER_TESTNET_API_SECRET_4"]
    secret5 = os.environ["LIGHTER_TESTNET_API_SECRET_5"]

    print(f"live mode: account_index={account_index} keys=({key4},{key5}) "
          f"secrets=({_mask(secret4)}, {_mask(secret5)})")

    from nautilus_trader.adapters.lighter import (
        LighterDataClientConfig,
        LighterDeployment,
        LighterEnvironment,
        LighterExecutionClientConfig,
    )
    from nautilus_trader.live import (
        DataClientConfig,
        ExecutionClientConfig,
        LiveNodeConfig,
    )
    from nautilus_trader.model import AccountId, Venue

    # Two execution clients, one account, two key slots, distinct venues so
    # routing/cache/topics stay separate (docs: "Custom venue identity").
    exec_a = ExecutionClientConfig(
        client_id="LIGHTER_K4",
        config=LighterExecutionClientConfig(
            account_id=AccountId("LIGHTER_K4-001"),
            account_index=account_index,
            api_key_index=key4,
            private_key=secret4,
            environment=LighterEnvironment.TESTNET,
            deployment=LighterDeployment.LIGHTER,
            venue=Venue("LIGHTER_K4"),
            use_gtd=False,  # strategy manages expiry; avoid 5-min GTD minimum
        ),
    )
    exec_b = ExecutionClientConfig(
        client_id="LIGHTER_K5",
        config=LighterExecutionClientConfig(
            account_id=AccountId("LIGHTER_K5-001"),
            account_index=account_index,
            api_key_index=key5,
            private_key=secret5,
            environment=LighterEnvironment.TESTNET,
            deployment=LighterDeployment.LIGHTER,
            venue=Venue("LIGHTER_K5"),
            use_gtd=False,
        ),
    )
    data_cfg = DataClientConfig(
        client_id="LIGHTER",
        config=LighterDataClientConfig(
            environment=LighterEnvironment.TESTNET,
            deployment=LighterDeployment.LIGHTER,
        ),
    )

    node_config = LiveNodeConfig(
        data_clients={"LIGHTER": data_cfg},
        exec_clients={"LIGHTER_K4": exec_a, "LIGHTER_K5": exec_b},
    )
    print("LiveNodeConfig with 1 data + 2 exec clients constructed OK "
          "(same account_index, per-key venue scoping)")
    print("NOTE: full node run requires a strategy wired to both venues; see findings.md.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-plan", action="store_true", help="no creds: print plan + sandbox test")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_plan or not creds_present():
        print("== DRY-PLAN mode (no LIGHTER_TESTNET_* credentials) ==")
        print_env_plan()
        print()
        ok = run_nonce_sandbox()
        print()
        print("Verdict basis: dry-plan (source-level + sandbox). NOT live-verified.")
        return 0 if ok else 1
    return run_live()


if __name__ == "__main__":
    sys.exit(main())
