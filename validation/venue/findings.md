# T4 — OKX Amend & clOrdId Idempotency: Research Findings

> 2026-11-10. Network: OKX unreachable from this host (DNS → 169.254.0.2); all
> source citations below are from third-party mirrors (GitHub, CDN) and web-
> search-accessible pages. No live API response was observed.

## 1. OKX Amend-Order Semantics

### 1.1 Endpoint & Parameters

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `instId` | String | Yes | e.g. `BTC-USDT-SWAP` |
| `ordId` | String | No* | Order ID; `ordId` or `clOrdId` required |
| `clOrdId` | String | No* | Client order ID |
| `reqId` | String | No | Request ID for idempotent amend |
| `newSz` | String | No | New **total** size including fills, not remaining |
| `newPx` | String | No | New price; must satisfy `tickSz` |

**Source**: [suenot/okx-docs-markdown](https://github.com/suenot/okx-docs-markdown/blob/main/docs/trading/trade.md) (mirror of OKX v5 docs), verified against [burakoner/OKX.Api](https://github.com/burakoner/OKX.Api/blob/master/docs/reference/trade.md).

### 1.2 Amend = Modify-in-Place, NOT Cancel-Replace

Evidence from the OKX Api .NET wrapper docs (Burakoner):

> *"Amendment is checked against the matching-engine snapshot when the
> command arrives, **with the order being amended still present in the
> book**."*

This is the critical phrase: the order remains in the book during the amend.
A cancel-replace would pull the order out first, then submit a new one.
OKX does **not** cancel-replace; they modify the existing order in situ.

**Inferences:**

| Question | Answer | Evidence Quality |
|----------|--------|-----------------|
| Does `ordId` change after amend? | **No** — response returns the same `ordId` | Response schema returns same `ordId` field (trade.md) |
| Does queue priority reset? | **[INFERENCE] Likely preserved**, since the order remains in the book during the amend check | The "still present" language is strong; no explicit queue-priority guarantee found |
| What about RPI orders? | `newSz` amend re-validates minimum notional; price-only amend skips notional recheck | [OKX log 2026-08-18](https://www.okx.com/docs-v5/log_en/#2026-08-18) |
| `CancelOnFail` | Default `false` — failed amend preserves original order | Burakoner docs + trade.md |
| Max in-flight amends | Error `51513`: max 3 amendments in progress per order | Burakoner docs |

**Queue-priority caveat**: No OKX document explicitly states "queue priority is preserved."
The "still present in the book" phrasing strongly implies time priority is maintained, but
a price change could affect queue position in a price-time priority model (typically, price
takes precedence; same-price orders retain original time). This remains **[INFERENCE]**
until verified with a live demo order (compare `cTime` / fill sequence before and after amend).

### 1.3 Rate Limits

| Scope | Limit | Source |
|-------|-------|--------|
| Single-order Place/Amend | 60 commands / 2 seconds | User ID + Instrument ID |
| Account-level aggregate | 1000 orders / 2 seconds (base) | VIP/fill-ratio tier adjusts |
| Amend only (earlier doc) | 5 requests / 2 seconds | OKX changelog — now superseded by 60/2s |
| REST and WS | **Shared** budget per command type | Place and Amend independent |
| `GET /api/v5/trade/order` | 60 requests / 2 seconds | Independent of Place/Amend |

**Source**: [burakoner/OKX.Api trade.md](https://github.com/burakoner/OKX.Api/blob/master/docs/reference/trade.md), cross-referenced with OKX changelog.

---

## 2. clOrdId Idempotency

### 2.1 clOrdId Rules

| Rule | Detail | Source |
|------|--------|--------|
| Characters | Case-sensitive alphanumeric, must start with letter | [OKX learn article](https://www.okx.com/en-us/learn/complete-guide-to-okex-api-v5-upgrade) |
| Max length | 32 characters | [nautilus issue #4116](https://github.com/nautechsystems/nautilus_trader/issues/4116) |
| Recommended format | `uuid4().hex` (32 chars, no hyphens) | [OKX skill doc](https://github.com/HimanshuJ16/Algo-Trading-Skills/blob/main/skills/okx-unified-account-api/SKILL.md) |
| Hyphens | **Rejected** (standard `uuid4()` is 36 chars) | nautilus #4116, OKX skill doc |
| Malformed rejection | Error `51000`: "Parameter clOrdId error" | nautilus #4116 |

### 2.2 Uniqueness Scope

> *"clOrdId must be unique among **current pending orders**."*
> — [alan-quant-lab okx_api_notes.md](https://github.com/alanships/alan-quant-lab/blob/main/docs/okx_api_notes.md)

> *"Once an order reaches a terminal state, OKX **may** allow reuse."*
> — same source

**Scope is per-account, not per-instrument.** The docs say "among current pending orders" with no
instrument qualifier. This means two different instruments with the same clOrdId in flight would collide.
Practice: always generate globally unique IDs.

### 2.3 Duplicate-Submit Behavior

| Scenario | Expected Behavior | Evidence |
|----------|-------------------|----------|
| Same clOrdId, first order still pending | **Reject** (error code TBD — live test needed) | "must be unique among current pending orders" |
| Same clOrdId, first order filled/cancelled | **[INFERENCE] May accept** — OKX "may allow reuse" | alan-quant-lab notes |
| Same clOrdId, retry after timeout | **Use same clOrdId** — this is the idempotency contract | OKX skill doc §8: "Mint and persist a clOrdId before submitting" |
| Pre-retry check | Query `GET /api/v5/trade/order?clOrdId=...` to see if original was accepted | OKX skill doc §8 |
| Order query returns `51603` | Order does not exist — safe to re-place | alan-quant-lab notes |

**Critical gap**: The **exact error code for duplicate clOrdId on placement** is not documented in
any source we could reach. It could be `51000` (general parameter error), a dedicated code, or even
`50004`/`50011`. **This must be observed live.** The test script logs the exact `sCode` and `sMsg`.

### 2.4 Double-Fill Risk

> *"clOrdId is the only idempotency handle OKX offers."*
> — OKX skill doc

If a client times out and **does not** reuse the same clOrdId on retry, it submits a **second order**
with a new clOrdId — potentially doubling the position. The idempotency contract says: reuse the
**same** clOrdId, and query by clOrdId before retrying.

**[INFERENCE]**: With correct clOrdId reuse, double-fill risk is very low because:
1. If the original was accepted, the duplicate is rejected
2. If the original was NOT accepted, the retry places a single order

But this is untested. The demo test MUST verify the duplicate-reject error code.

### 2.5 Key Sources

| Source | URL |
|--------|-----|
| alan-quant-lab notes | <https://github.com/alanships/alan-quant-lab/blob/main/docs/okx_api_notes.md> |
| OKX unified account skill | <https://github.com/HimanshuJ16/Algo-Trading-Skills/blob/main/skills/okx-unified-account-api/SKILL.md> |
| Nautilus #4116 (clOrdId format) | <https://github.com/nautechsystems/nautilus_trader/issues/4116> |
| suenot/okx-docs-markdown mirror | <https://github.com/suenot/okx-docs-markdown/blob/main/docs/trading/trade.md> |
| Burakoner OKX.Api trade.md | <https://github.com/burakoner/OKX.Api/blob/master/docs/reference/trade.md> |
| ccxt OKX error codes | <https://github.com/ccxt/ccxt/blob/master/python/ccxt/okx.py> |
| OKX changelog | <https://www.okx.com/docs-v5/log_en/> (unreachable; content via Burakoner mirror) |

---

## 3. Lighter `client_order_index` Idempotency (Bonus)

### 3.1 Protocol

Lighter uses an **API-key-scoped nonce** (not the same as `client_order_index`). Each API key
(index 4–254) has its own nonce that must be **strictly sequential**. Sending orders in parallel
without waiting for responses causes `21104: "invalid nonce"`.

**Key facts:**

| Fact | Detail | Source |
|------|--------|--------|
| API key indices | 4–254 for programmatic; 0–3 reserved; 255 sentinel | [Lighter get-started](https://apidocs.lighter.xyz/docs/get-started) (unreachable; via nautilus docs) |
| Nonce error | `21104: "invalid nonce"` on out-of-order submissions | [ccxt #28073](https://github.com/ccxt/ccxt/issues/28073) |
| `client_order_index` | int64, user-assigned, used to query orders via `GET /api/v1/accountOrders?client_order_indexes=...` | [Lighter-python OrderApi](https://github.com/elliottech/lighter-python/blob/main/docs/OrderApi.md) |
| Testing endpoint | `POST /api/v1/sendTx` | ccxt #28073 |
| Testnet URL | `https://testnet.zklighter.elliot.ai` | PLAN.zh.md + nautilus docs |

### 3.2 Duplicate `client_order_index` Behavior

**[INFERENCE]**: Lighter's nonce is per-API-key, not per `client_order_index`. If you send two
transactions with the same nonce (which the signer would prevent if used correctly), the second
would get `21104`. But if you manage to send the **same payload** twice (e.g. a retry scenario
where the first TX was accepted but timed out), the behavior depends on whether Lighter deduplicates
by `client_order_index`:

- Lighter is a zk-rollup; orders are L2 transactions
- Each transaction must have a unique nonce
- The `client_order_index` field is for client-side tracking, not server-side dedup
- **Likely no server-side idempotency by `client_order_index`** — the nonce is the real dedup mechanism

**This must be verified on testnet.** The test script should:
1. Place an order, note the nonce used
2. Try to place another order with the same nonce (expected: reject)
3. Place an order, let it succeed, then try to place with the same `client_order_index` but different nonce (expected: accept — two separate orders)

### 3.3 API Authentication

Lighter uses Schnorr signatures (EdDSA over ECgFp5), not HMAC. The official SDK or `lighter-python`
package is required. No simple `curl` + API key approach works. The Nautilus adapter uses the Rust
signer compiled to WASM.

**Credentials needed**: `LIGHTER_API_PRIVATE_KEY`, `LIGHTER_ACCOUNT_INDEX`, `LIGHTER_API_KEY_INDEX`.

**Verdict**: The Lighter test requires the `lighter-python` SDK or nautilus_trader. Document the
endpoint and payload shape; mark as **BLOCKED** until keys are available.

---

## 4. Coverage Gaps (Must Test Live)

| Gap | Why | How to Resolve |
|-----|-----|----------------|
| Duplicate clOrdId error code | No source documents the exact `sCode` | Demo: submit same clOrdId twice, capture `sCode` |
| Queue priority preservation | "Still present" ≠ explicit guarantee | Demo: place order, note `cTime`, amend price 1 tick away, check if fills interleave with same-price unfilled orders |
| Amend with `newSz=0` | Not documented — does it cancel? | Demo: test edge case |
| Lighter `client_order_index` dedup | No explicit documentation found | Testnet: duplicate index with different nonce |
| `CancelOnFail=true` amend behavior | Documented but untested | Demo: amend with invalid price, verify original is cancelled |