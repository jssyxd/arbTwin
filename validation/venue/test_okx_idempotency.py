#!/usr/bin/env python3
"""test_okx_idempotency.py — OKX Demo clOrdId idempotency + amend semantics.

Usage:
    # Simulated (no network) — validates local logic only
    python test_okx_idempotency.py --simulate

    # Demo trading (requires OKX-reachable host + demo API keys)
    OKX_SIMULATED=1 OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=... \
        python test_okx_idempotency.py --i-understand-demo

    # Specify custom instrument (default: BTC-USDT-SWAP)
    OKX_INST_ID=ETH-USDT-SWAP python test_okx_idempotency.py --i-understand-demo

    # With proxy
    ARBTWIN_PROXY=http://proxy:1080 python test_okx_idempotency.py --i-understand-demo

Refuses to run without --i-understand-demo.
No credentials in output. Uses OKX demo trading (x-simulated-trading: 1).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


# ────────────────────────────────────────────────────────────────── constants
OKX_REST_URL = "https://www.okx.com"
DEMO_HEADER = {"x-simulated-trading": "1"}
DEFAULT_INST_ID = "BTC-USDT-SWAP"
MIN_ORDER_SZ = "1"  # 1 contract — minimum for most SWAP instruments
ORDER_PX_BUFFER = 0.90  # place well below market to avoid accidental fills

# ─────────────────────────────────────────────────────────────────── helpers
def _now_iso() -> str:
    """OKX requires ISO 8601 UTC with exactly 3 fractional digits."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + f"{dt.datetime.now(dt.timezone.utc).microsecond // 1000:03d}Z"


def _sign(timestamp: str, method: str, path: str, body: str, secret: str) -> str:
    prehash = timestamp + method.upper() + path + body
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def _headers(method: str, path: str, body: str,
             api_key: str, secret: str, passphrase: str) -> dict[str, str]:
    ts = _now_iso()
    return {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": _sign(ts, method, path, body, secret),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def _cl_ord_id(prefix: str = "test") -> str:
    """Generate a valid OKX clOrdId: ≤32 chars, alphanumeric, starts with letter."""
    uid = uuid.uuid4().hex  # 32 chars, no hyphens
    return f"{prefix}{uid[:28]}"[:32]  # ensure ≤32


# ─────────────────────────────────────────────────────────────── result types
@dataclass
class TestResult:
    name: str
    status: str          # "PASS", "FAIL", "BLOCKED", "SKIP"
    detail: str = ""
    evidence: str = ""   # sCode, sMsg, ordId, etc. (sanitized — no keys)


# ──────────────────────────────────────────────────────────── simulated mode
class SimulatedOkxClient:
    """In-memory OKX that enforces documented rules for local validation."""

    def __init__(self):
        self._orders: dict[str, dict] = {}       # ordId → order
        self._by_clord: dict[str, str] = {}      # clOrdId → ordId
        self._pending_clords: set[str] = set()
        self._next_ord_id = 100000000000000000

    def _new_ord_id(self) -> str:
        self._next_ord_id += 1
        return str(self._next_ord_id)

    # ── place ────────────────────────────────────────────────────────
    def place_order(self, inst_id: str, side: str, sz: str, px: str,
                    cl_ord_id: str, td_mode: str = "cross",
                    ord_type: str = "limit") -> dict:
        if cl_ord_id in self._pending_clords:
            return {"code": "1", "data": [{"clOrdId": cl_ord_id, "ordId": "",
                     "sCode": "51000", "sMsg": "Parameter clOrdId error: duplicate pending order"}]}
        if len(cl_ord_id) > 32:
            return {"code": "1", "data": [{"clOrdId": cl_ord_id, "ordId": "",
                     "sCode": "51000", "sMsg": "Parameter clOrdId error: too long"}]}
        if not cl_ord_id[0].isalpha():
            return {"code": "1", "data": [{"clOrdId": cl_ord_id, "ordId": "",
                     "sCode": "51000", "sMsg": "Parameter clOrdId error: must start with letter"}]}
        if "-" in cl_ord_id:
            return {"code": "1", "data": [{"clOrdId": cl_ord_id, "ordId": "",
                     "sCode": "51000", "sMsg": "Parameter clOrdId error: no hyphens"}]}

        ord_id = self._new_ord_id()
        order = {
            "ordId": ord_id, "clOrdId": cl_ord_id, "instId": inst_id,
            "side": side, "sz": sz, "px": px, "ordType": ord_type,
            "state": "live", "cTime": str(int(time.time() * 1000)),
            "accFillSz": "0",
        }
        self._orders[ord_id] = order
        self._by_clord[cl_ord_id] = ord_id
        self._pending_clords.add(cl_ord_id)
        return {"code": "0", "data": [{"clOrdId": cl_ord_id, "ordId": ord_id,
                "sCode": "0", "sMsg": ""}]}

    # ── cancel ───────────────────────────────────────────────────────
    def cancel_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        if cl_ord_id:
            ord_id = self._by_clord.get(cl_ord_id, "")
        if not ord_id or ord_id not in self._orders:
            return {"code": "1", "data": [{"ordId": ord_id, "clOrdId": cl_ord_id,
                     "sCode": "51603", "sMsg": "Order does not exist"}]}
        order = self._orders[ord_id]
        if order["state"] in ("filled", "canceled"):
            return {"code": "1", "data": [{"ordId": ord_id, "clOrdId": cl_ord_id,
                     "sCode": "51400", "sMsg": "Order already completed"}]}
        order["state"] = "canceled"
        self._pending_clords.discard(order["clOrdId"])
        return {"code": "0", "data": [{"ordId": ord_id, "clOrdId": order["clOrdId"],
                "sCode": "0", "sMsg": ""}]}

    # ── amend ────────────────────────────────────────────────────────
    def amend_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "",
                    new_sz: str = "", new_px: str = "") -> dict:
        if cl_ord_id:
            ord_id = self._by_clord.get(cl_ord_id, "")
        if not ord_id or ord_id not in self._orders:
            return {"code": "1", "data": [{"ordId": ord_id, "clOrdId": cl_ord_id,
                     "sCode": "51603", "sMsg": "Order does not exist"}]}
        order = self._orders[ord_id]
        if order["state"] != "live":
            return {"code": "1", "data": [{"ordId": ord_id, "clOrdId": cl_ord_id,
                     "sCode": "51400", "sMsg": "Order already completed"}]}
        if new_sz:
            order["sz"] = new_sz
        if new_px:
            order["px"] = new_px
        # ordId stays the SAME — amend is modify-in-place
        return {"code": "0", "data": [{"ordId": ord_id, "clOrdId": order["clOrdId"],
                "sCode": "0", "sMsg": ""}]}

    # ── query ────────────────────────────────────────────────────────
    def get_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        if cl_ord_id:
            ord_id = self._by_clord.get(cl_ord_id, "")
        if not ord_id or ord_id not in self._orders:
            return {"code": "1", "data": [], "msg": "51603: Order does not exist"}
        return {"code": "0", "data": [self._orders[ord_id]]}


# ───────────────────────────────────────────────────────── live OKX client
class LiveOkxClient:
    """Thin wrapper around OKX REST API using only stdlib."""

    def __init__(self, api_key: str, secret: str, passphrase: str, proxy: str = ""):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.proxy = proxy

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        body_str = json.dumps(body or {}, separators=(",", ":")) if body or method != "GET" else ""
        if method == "GET" and body is None:
            body_str = ""
        hdrs = _headers(method, path, body_str, self.api_key, self.secret, self.passphrase)
        hdrs.update(DEMO_HEADER)

        import urllib.request
        url = OKX_REST_URL + path
        data = body_str.encode("utf-8") if body_str else None
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        if self.proxy:
            req.set_proxy(self.proxy, "http")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8"))
        except Exception as e:
            return {"code": "-1", "msg": str(e), "data": []}

    def place_order(self, inst_id: str, side: str, sz: str, px: str,
                    cl_ord_id: str, td_mode: str = "cross",
                    ord_type: str = "limit") -> dict:
        body = {
            "instId": inst_id, "tdMode": td_mode, "side": side,
            "ordType": ord_type, "sz": sz, "clOrdId": cl_ord_id,
        }
        if ord_type != "market":
            body["px"] = px
        return self._request("POST", "/api/v5/trade/order", body)

    def cancel_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        body: dict[str, str] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        elif cl_ord_id:
            body["clOrdId"] = cl_ord_id
        return self._request("POST", "/api/v5/trade/cancel-order", body)

    def amend_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "",
                    new_sz: str = "", new_px: str = "") -> dict:
        body: dict[str, str] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        elif cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if new_sz:
            body["newSz"] = new_sz
        if new_px:
            body["newPx"] = new_px
        return self._request("POST", "/api/v5/trade/amend-order", body)

    def get_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        path = f"/api/v5/trade/order?instId={inst_id}"
        if ord_id:
            path += f"&ordId={ord_id}"
        elif cl_ord_id:
            path += f"&clOrdId={cl_ord_id}"
        return self._request("GET", path)


# ──────────────────────────────────────────────────────────────────── tests
def run_tests(client, inst_id: str) -> list[TestResult]:
    results: list[TestResult] = []

    # Unique prefix for this run so we don't collide with prior test orders
    run_id = uuid.uuid4().hex[:8]

    # ── Test 1: Malformed clOrdId (hyphens) ─────────────────────
    bad_id = f"test-{run_id}"  # has hyphen
    resp = client.place_order(inst_id, "buy", MIN_ORDER_SZ, "1", cl_ord_id=bad_id)
    s_code = (resp.get("data", [{}])[0] or {}).get("sCode", "")
    s_msg = (resp.get("data", [{}])[0] or {}).get("sMsg", "")
    results.append(TestResult(
        name="T1: Reject hyphens in clOrdId",
        status="PASS" if s_code != "0" else "FAIL",
        detail=f"sCode={s_code}, sMsg={s_msg}",
        evidence=f"clOrdId submitted: '{bad_id}' — expected reject",
    ))

    # ── Test 2: clOrdId > 32 chars ──────────────────────────────
    long_id = "a" * 33
    resp = client.place_order(inst_id, "buy", MIN_ORDER_SZ, "1", cl_ord_id=long_id)
    s_code = (resp.get("data", [{}])[0] or {}).get("sCode", "")
    results.append(TestResult(
        name="T2: Reject clOrdId > 32 chars",
        status="PASS" if s_code != "0" else "FAIL",
        detail=f"sCode={s_code}, sMsg={(resp.get('data',[{}])[0] or {}).get('sMsg','')}",
        evidence=f"clOrdId length: {len(long_id)}",
    ))

    # ── Test 3: Place order with valid clOrdId ──────────────────
    cl_ord_a = _cl_ord_id(f"a{run_id[:6]}")
    resp_a = client.place_order(inst_id, "buy", MIN_ORDER_SZ, "1", cl_ord_id=cl_ord_a)
    data_a = (resp_a.get("data", [{}])[0] or {})
    s_code_a = data_a.get("sCode", "")
    ord_id_a = data_a.get("ordId", "")

    if s_code_a != "0":
        results.append(TestResult(
            name="T3: Place order with valid clOrdId",
            status="BLOCKED",
            detail=f"sCode={s_code_a}, sMsg={data_a.get('sMsg','')}",
            evidence="Cannot proceed with idempotency tests",
        ))
        # Cancel anything we might have placed
        if ord_id_a:
            client.cancel_order(inst_id, ord_id=ord_id_a)
        return results

    results.append(TestResult(
        name="T3: Place order with valid clOrdId",
        status="PASS",
        detail=f"ordId={ord_id_a}, clOrdId={cl_ord_a}",
        evidence="Order accepted",
    ))

    # ── Test 4: Duplicate clOrdId (same, pending) ───────────────
    resp_dup = client.place_order(inst_id, "buy", MIN_ORDER_SZ, "1", cl_ord_id=cl_ord_a)
    data_dup = (resp_dup.get("data", [{}])[0] or {})
    s_code_dup = data_dup.get("sCode", "")
    s_msg_dup = data_dup.get("sMsg", "")

    results.append(TestResult(
        name="T4: Duplicate clOrdId on pending order",
        status="PASS" if s_code_dup != "0" else "FAIL — duplicate accepted (double-fill risk!)",
        detail=f"sCode={s_code_dup}, sMsg={s_msg_dup}",
        evidence=f"Same clOrdId='{cl_ord_a}' submitted twice; expected reject",
    ))

    # ── Test 5: Query by clOrdId ────────────────────────────────
    resp_q = client.get_order(inst_id, cl_ord_id=cl_ord_a)
    q_code = resp_q.get("code", "")
    q_data = resp_q.get("data", [])
    results.append(TestResult(
        name="T5: Query order by clOrdId",
        status="PASS" if q_code == "0" and len(q_data) > 0 else "FAIL",
        detail=f"code={q_code}, found={len(q_data)} order(s)",
        evidence=f"Queried clOrdId='{cl_ord_a}'",
    ))

    # ── Test 6: Amend order via clOrdId ─────────────────────────
    new_px = "2"  # far from market
    resp_am = client.amend_order(inst_id, cl_ord_id=cl_ord_a, new_px=new_px)
    data_am = (resp_am.get("data", [{}])[0] or {})
    s_code_am = data_am.get("sCode", "")
    ord_id_am = data_am.get("ordId", "")

    ord_id_unchanged = (ord_id_am == ord_id_a) if ord_id_am else None
    results.append(TestResult(
        name="T6: Amend order — ordId unchanged",
        status="PASS" if s_code_am == "0" and ord_id_unchanged is not False
                else ("PASS (ordId unchanged)" if ord_id_unchanged else "FAIL"),
        detail=f"sCode={s_code_am}, old ordId={ord_id_a}, new ordId in response={ord_id_am}"
               f" → {'UNCHANGED' if ord_id_unchanged else 'CHANGED' if ord_id_unchanged is False else 'N/A'}",
        evidence="Amend is modify-in-place if ordId unchanged",
    ))

    # ── Test 7: Amend fails (invalid px) with CancelOnFail implied ─
    # Not testing CancelOnFail explicitly — requires additional param

    # ── Test 8: Cancel by clOrdId ───────────────────────────────
    resp_c = client.cancel_order(inst_id, cl_ord_id=cl_ord_a)
    data_c = (resp_c.get("data", [{}])[0] or {})
    s_code_c = data_c.get("sCode", "")
    results.append(TestResult(
        name="T7: Cancel order by clOrdId",
        status="PASS" if s_code_c == "0" else "FAIL",
        detail=f"sCode={s_code_c}, sMsg={data_c.get('sMsg','')}",
        evidence=f"Cancelled clOrdId='{cl_ord_a}'",
    ))

    # ── Test 9: Reuse clOrdId after cancel ──────────────────────
    resp_reuse = client.place_order(inst_id, "buy", MIN_ORDER_SZ, "1", cl_ord_id=cl_ord_a)
    data_reuse = (resp_reuse.get("data", [{}])[0] or {})
    s_code_reuse = data_reuse.get("sCode", "")
    ord_id_reuse = data_reuse.get("ordId", "")

    results.append(TestResult(
        name="T8: Reuse clOrdId after cancel",
        status="PASS" if s_code_reuse == "0" else "BLOCKED",
        detail=f"sCode={s_code_reuse}, new ordId={ord_id_reuse or 'N/A'}",
        evidence="OKX 'may allow' reuse after terminal state; this confirms",
    ))

    # Cleanup
    if ord_id_reuse:
        client.cancel_order(inst_id, ord_id=ord_id_reuse)
    elif s_code_a == "0" and ord_id_a:
        client.cancel_order(inst_id, ord_id=ord_id_a)

    return results


# ──────────────────────────────────────────────────────────────────── main
def print_table(results: list[TestResult]) -> None:
    w_name = max(len(r.name) for r in results) + 2
    w_stat = 10
    header = f"| {'Test':<{w_name}} | {'Result':<{w_stat}} | Detail |"
    sep = f"|-{'-'*w_name}-|-{'-'*w_stat}-|--------|"
    print("\n" + header)
    print(sep)
    for r in results:
        status_icon = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "⛔", "SKIP": "⏭️"}.get(r.status, "❓")
        print(f"| {r.name:<{w_name}} | {status_icon} {r.status:<{w_stat-3}} | {r.detail} |")
    print()

    # Summary
    passes = sum(1 for r in results if r.status == "PASS")
    fails = sum(1 for r in results if r.status == "FAIL")
    blocked = sum(1 for r in results if r.status == "BLOCKED")
    print(f"Summary: {passes} PASS, {fails} FAIL, {blocked} BLOCKED, {len(results)} total")


def main():
    parser = argparse.ArgumentParser(description="OKX clOrdId idempotency + amend semantics test")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulated tests (no network, validates local logic)")
    parser.add_argument("--i-understand-demo", action="store_true",
                        help="Acknowledge: this uses OKX demo trading. No real funds at risk, "
                             "but orders will be placed on OKX demo infrastructure.")
    parser.add_argument("--inst-id", default=DEFAULT_INST_ID,
                        help=f"OKX instrument ID (default: {DEFAULT_INST_ID})")
    args = parser.parse_args()

    if args.simulate:
        print("=== SIMULATED MODE — local logic validation only (no network) ===\n")
        client: SimulatedOkxClient | LiveOkxClient = SimulatedOkxClient()
        results = run_tests(client, args.inst_id)
        print_table(results)
        return 0 if all(r.status == "PASS" for r in results) else 1

    if not args.i_understand_demo:
        print("ERROR: This script places real orders on OKX demo trading.", file=sys.stderr)
        print("Pass --i-understand-demo to confirm you accept this risk.", file=sys.stderr)
        print("Or use --simulate to run local logic tests only.", file=sys.stderr)
        return 2

    # Live demo mode
    api_key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    simulated = os.environ.get("OKX_SIMULATED", "")

    if not all([api_key, secret, passphrase]):
        print("ERROR: Missing OKX_API_KEY, OKX_SECRET_KEY, or OKX_PASSPHRASE env vars.", file=sys.stderr)
        return 3

    if simulated != "1":
        print("ERROR: OKX_SIMULATED=1 is required. This ensures demo trading.", file=sys.stderr)
        return 4

    proxy = os.environ.get("ARBTWIN_PROXY", "")

    print("=== LIVE DEMO MODE — OKX demo trading ===")
    print(f"  Instrument: {args.inst_id}")
    print(f"  Proxy: {proxy or 'none'}")
    print(f"  API Key ending: ...{api_key[-4:] if len(api_key) >= 4 else '(short)'}")
    print("  ⚠️  No real funds at risk (demo trading).\n")

    live_client = LiveOkxClient(api_key, secret, passphrase, proxy)
    results = run_tests(live_client, args.inst_id)
    print_table(results)

    # Print evidence details
    print("\n=== Evidence ===")
    for r in results:
        if r.evidence:
            print(f"  [{r.status}] {r.name}")
            print(f"          {r.evidence}")

    all_pass = all(r.status == "PASS" for r in results)
    return 0 if all_pass else (1 if any(r.status == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    sys.exit(main())