# T4 — OKX Amend + clOrdId Idempotency: Run Checklist

> For execution on a Linux VPS/CI with OKX network access.
> Host `W:/dynamicTWIN` cannot reach `api.okx.com` (DNS → 169.254.0.2).

## Prerequisites

| # | Requirement | Status |
|---|-------------|--------|
| P1 | Linux host that can reach `api.okx.com:443` and `ws.okx.com:443` | ⬜ |
| P2 | Python 3.10+ (stdlib only; no `requests`, no `pip install`) | ⬜ |
| P3 | OKX **demo** API key with Trade permission (Read+Trade) | ⬜ |
| P4 | OKX demo account with some USDT balance (≥10 USDT recommended) | ⬜ |
| P5 | (Optional) `ARBTWIN_PROXY` env var if behind a proxy | ⬜ |
| P6 | (Optional) `lighter-python` SDK + Lighter testnet key for §3 | ⬜ |

**DO NOT** use a live/production OKX API key. The script enforces `OKX_SIMULATED=1`;
it will refuse to run without it.

### How to get OKX demo keys

1. Go to OKX Demo Trading: https://www.okx.com/trade-market/demo
2. Click "Personal Center" → "Create Demo Account API key"
3. Set permissions to **Trade** (Read + Trade). Do NOT enable Withdraw.
4. Save the API key, secret key, and passphrase.
5. Note: demo keys work ONLY with `x-simulated-trading: 1` header.

## Commands

### Step 0: Verify reachability

```bash
curl -s -o /dev/null -w "%{http_code}" https://www.okx.com/api/v5/public/time
# Expected: 200
```

### Step 1: Run simulated tests first

```bash
cd /path/to/dynamicTWIN
python validation/venue/test_okx_idempotency.py --simulate
```

Expected: 8/8 PASS. If this fails, the test script has a bug — do not proceed.

### Step 2: Run demo trading tests

```bash
OKX_SIMULATED=1 \
OKX_API_KEY="your-demo-api-key" \
OKX_SECRET_KEY="your-demo-secret" \
OKX_PASSPHRASE="your-demo-passphrase" \
python validation/venue/test_okx_idempotency.py --i-understand-demo

# With custom instrument:
OKX_INST_ID=ETH-USDT-SWAP python validation/venue/test_okx_idempotency.py --i-understand-demo

# With proxy:
ARBTWIN_PROXY=http://proxy:1080 python validation/venue/test_okx_idempotency.py --i-understand-demo
```

**Never pipe credentials.** Use `export` or a `.env` file that is `.gitignore`d.
The script does NOT print keys; it prints only the last 4 chars of the API key.

### Step 3: (Optional) Lighter testnet test

```bash
python validation/venue/test_okx_idempotency.py --lighter \
  --lighter-api-key-index 4 \
  --lighter-account-index YOUR_ACCOUNT_INDEX
```

(Requires `lighter-python` SDK installed.)

## Expected Evidence

### Test T1–T2: clOrdId validation

| Test | Expected OKX response |
|------|----------------------|
| T1: Hyphens | `sCode=51000, "Parameter clOrdId error"` |
| T2: >32 chars | `sCode=51000, "Parameter clOrdId error"` |

### Test T3: Place order

Expected: `sCode=0, ordId=(numeric string)`.

### Test T4: Duplicate clOrdId

| Possible Outcome | Interpretation |
|------------------|----------------|
| `sCode ≠ 0` (rejected) | ✅ OKX enforces uniqueness among pending orders — safe for idempotent retry |
| `sCode = 0` (accepted) | ❌ OKX accepted a duplicate; double-fill possible on retry |

**Record the exact `sCode` and `sMsg`.** This is the most important result.

### Test T5: Query by clOrdId

Expected: `code=0, data` contains the order placed in T3.

### Test T6: Amend ordId unchanged

| Possible Outcome | Interpretation |
|------------------|----------------|
| Response `ordId` == original `ordId` | ✅ Amend is modify-in-place; queue priority likely preserved |
| Response `ordId` ≠ original `ordId` | ❌ Amend is cancel-replace; queue priority resets |

### Test T7: Cancel by clOrdId

Expected: `sCode=0`. Confirms clOrdId is usable for cancellation.

### Test T8: Reuse clOrdId after cancel

| Possible Outcome | Interpretation |
|------------------|----------------|
| `sCode=0` (accepted) | OKX allows reuse after terminal state |
| `sCode≠0` (rejected) | OKX permanently reserves the clOrdId; generate new ones |

## Cleanup

After running, verify no orders are left pending:

```bash
# Query open orders (manual or via script extension)
# The test script cancels all orders it creates, but verify:
curl -s -H "x-simulated-trading: 1" \
  -H "OK-ACCESS-KEY: ..." ... \
  "https://www.okx.com/api/v5/trade/orders-pending?instId=BTC-USDT-SWAP"
```

## Results Table

Fill in after running:

| Test | Status | sCode | sMsg / Notes |
|------|--------|-------|--------------|
| P1: OKX reachable | RUN / BLOCKED | — | curl to `/public/time` |
| T1: Reject hyphens | RUN / BLOCKED | | |
| T2: Reject >32 chars | RUN / BLOCKED | | |
| T3: Place valid order | RUN / BLOCKED | | |
| T4: Duplicate clOrdId | RUN / BLOCKED | | **Key result** |
| T5: Query by clOrdId | RUN / BLOCKED | | |
| T6: Amend ordId unchanged | RUN / BLOCKED | | **Key result** |
| T7: Cancel by clOrdId | RUN / BLOCKED | | |
| T8: Reuse after cancel | RUN / BLOCKED | | |
| **Lighter** (bonus) | RUN / BLOCKED / N/A | | Requires SDK + keys |

## Interpretation Guide

### If T4 rejects duplicate clOrdId (sCode ≠ 0):
- Retry safety: ✅ Reuse same clOrdId on timeout → OKX rejects duplicate → no double-fill.
- Recommended pattern: persist clOrdId before submit; on timeout, query by clOrdId; if `51603`, resend.

### If T4 accepts duplicate (sCode = 0):
- **Critical risk**: timeout retry could double your position.
- Mitigation: always `cancel_order(clOrdId=...)` before retrying, or always query first.

### If T6 ordId is unchanged:
- Amend is modify-in-place. Time/queue priority preserved for same-price amends.
- [INFERENCE]: price changes within the same price level keep queue position.
- Price changes to a new level: standard price-time priority — you join the new level's queue.

### T8 Terminal reuse:
- If OKX allows reuse after cancel/fill, you can recycle clOrdId formats.
- Regardless: the SDK should always generate unique IDs to avoid ambiguous reconciliation.