# T4 — OKX Amend + clOrdId Idempotency: Reply

> 2026-11-10. Network: OKX unreachable from this host.

## Deliverables

| File | Purpose | Status |
|------|---------|--------|
| `validation/venue/findings.md` | Research: amend semantics, clOrdId idempotency, Lighter analogue | ✅ Written |
| `validation/venue/test_okx_idempotency.py` | Runnable test script (simulated + live demo) | ✅ Written + verified |
| `validation/venue/checklist.md` | VPS/network/credential prerequisites, commands, interpretation guide | ✅ Written |
| `validation/venue/reply.md` | This summary | ✅ Written |

## Key Findings

### 1. OKX Amend = Modify-in-Place (NOT Cancel-Replace)

Evidence from OKX API documentation (via Burakoner mirror):

> *"Amendment is checked against the matching-engine snapshot when the command
> arrives, with the order being amended still present in the book."*

- `ordId` **does not change** in the amend response — confirmed in response schema
- Queue priority: **[INFERENCE] likely preserved** for same-price amends since order stays in book
- `newSz` is total size including fills, not remaining qty
- `CancelOnFail` defaults to `false` (original order preserved on failed amend)
- Rate limit: 60 commands/2s per User+Instrument (shared REST+WS)

### 2. clOrdId Idempotency

- Uniqueness scope: **per-account** (among pending orders, not per instrument)
- Duplicate behavior: "must be unique among current pending orders" → expected reject
- The **exact error code** for duplicate clOrdId is undocumented in any source we could reach — must be observed live
- Format: ≤32 chars, alphanumeric, start with letter, no hyphens → `uuid4().hex`
- Error `51000` = malformed clOrdId; `51603` = order not found
- Once terminal state, OKX "may allow" reuse
- Retry pattern: persist clOrdId → submit → on timeout, query `GET /api/v5/trade/order?clOrdId=...` → if `51603`, resubmit with same clOrdId

### 3. Lighter Analogue (Bonus)

- Lighter uses **API-key-scoped nonces** (sequential), not `client_order_index`, as the idempotency boundary
- `client_order_index` (int64) is for client-side tracking/query, not server-side dedup
- Error `21104: "invalid nonce"` if nonces arrive out of order
- Test endpoint: `POST /api/v1/sendTx` → `https://testnet.zklighter.elliot.ai`
- Requires `lighter-python` SDK or nautilus_trader (Schnorr signatures, not HMAC)
- **BLOCKED** until Lighter testnet key available

## Test Script Verification

Simulated mode (8/8 PASS):
```
T1: Reject hyphens in clOrdId            ✅ PASS
T2: Reject clOrdId > 32 chars            ✅ PASS
T3: Place order with valid clOrdId       ✅ PASS
T4: Duplicate clOrdId on pending order   ✅ PASS
T5: Query order by clOrdId               ✅ PASS
T6: Amend order — ordId unchanged        ✅ PASS
T7: Cancel order by clOrdId              ✅ PASS
T8: Reuse clOrdId after cancel           ✅ PASS
```

Live guard: script correctly refuses without `--i-understand-demo`.

## What Remains BLOCKED

| Item | Reason |
|------|--------|
| All live OKX tests | Host cannot reach `api.okx.com` (DNS → 169.254.0.2) |
| Lighter testnet tests | No testnet API key available |
| Queue priority confirmation | Cannot observe live order fill sequence |
| Exact duplicate clOrdId error code | Must be observed from live rejection |

## Next Step for User

1. Take `validation/venue/test_okx_idempotency.py` to an OKX-reachable VPS
2. Run `--simulate` first to verify Python environment
3. Run with demo keys and `--i-understand-demo`
4. Fill in the results table in `checklist.md`
5. Report the T4 duplicate clOrdId `sCode` — that's the key unknown