# T1 — OU half-life & threshold calibration (reachable data only)

Date: 2026-11-10 · Author: T1 worker · All numbers reproducible from commands listed below.

## 0. TL;DR

- Lighter mainnet 1m candles are **fully reachable and deep (90+ days)** for all
  12 approved symbols + BTC/ETH. The one genuine two-venue basis measurable today
  is **Lighter ↔ Binance** (Binance Vision monthly 1m klines, Aug–Oct 2026):
  **τ½ ≈ 6 min, σ ≈ 2 bps** on BTC/ETH — the basis reverts far faster and is far
  tighter than the repo's hourly estimates (0.8–1.7 h, σ 4–7.5 bps).
- The 12 approved symbols only have a **single-venue** series here (OKX DNS-poisoned,
  Binance Vision has no RWA/stock perps), so their OU numbers are a **price-vs-EMA
  deviation**, not a venue basis. They set an *upper bound* on σ and a scale
  reference for the machinery; the real per-symbol basis numbers wait for OKX.
- **OKX: nothing fetched.** `www.okx.com`/`api.okx.com` DNS-poisoned from this box
  (verified today, matches PLAN.zh.md §0). The Kaggle OKX dataset found
  (`thirtysevenhhhh/okx-15m-ohlcv-v2`) covers 2025-04→2026-05 **with zero overlap**
  with the Lighter window (2026-08→2026-11). No paired OKX–Lighter result is claimed.

## 1. Data inventory (actually downloaded, `validation/ou/data/`)

| File | What | Coverage | Rows | How |
|---|---|---|---:|---|
| `markets_orderBookDetails.json` | Lighter market map (id↔symbol, fees) | 243 markets, 2026-11-10 | 243 | `curl .../api/v1/orderBookDetails` |
| `{SYM}_1m.json` ×14 | Lighter mainnet 1m trade candles | 2026-08-08/11 → 2026-11-09 | 129,693–134,592 each | `python validation/ou/fetch_lighter_candles.py --kind trade --days 90` (batched 5 at a time) |
| `binance/BTCUSDT-1m-2026-{08,09,10}.zip`, `ETHUSDT-…` | Binance USDT-M perp 1m klines | 2026-08-01 → 2026-10-31 | 131,040 per symbol | `data.binance.vision` monthly zips |
| `binance/{BTCUSDT,ETHUSDT}_1m.parquet` | parsed+merged from the zips | as above | 131,040 | parse script (§5) |
| `binance/{BTC,ETH}_cross_venue_ou.json` | OU results on the Lighter↔Binance basis | overlap 82.3 d, 116,079/116,050 aligned minutes | — | §5 |
| `binance/cross_venue_scale.json` | same basis at 5m/60m | — | — | §5 |
| `okx_BTC-USDT-SWAP_15m.parquet` | Kaggle OKX 15m (test of external source) | **2025-04-30 → 2026-05-01**, 0 d overlap | 35,100 | Kaggle API (§4) |

Symbols: SOXL COIN ORCL SNDK LIT MSTR MRVL VVV XPT GRAM EWY TRUMP + BTC ETH.
Gap fraction inside each span: **0.000** (contiguous 1m bars; verified via
`build_basis` gap check).

Endpoint discovery (probed, not documented — `apidocs.lighter.xyz` unreachable
from this network, `docs.lighter.xyz` likewise):

```
GET https://mainnet.zklighter.elliot.ai/api/v1/candles
    ?market_id=<int>&resolution=1m&start_timestamp=<ms>&end_timestamp=<ms>&count_back=1500
GET https://mainnet.zklighter.elliot.ai/api/v1/markPriceCandles   # same params
```

- `resolution` ∈ {1m,5m,15m,30m,1h,4h,1d} — `1`, `60`, `1min`, `1s` all return `20001 invalid param`.
- `start_timestamp=0` rejected; use `1`. `end_timestamp` future ms is fine.
- Effective page size: **500 rows** when the window is wide (count_back>500).
- Backward paging works: `end_timestamp = oldest−1`, `start_timestamp = 1`.
- Rate limit: 4 parallel 90-day walks → **HTTP 429**, then a temporary **403** on
  `/api/v1/time`; sequential 2-worker walks with 1 s pause per 10 requests are stable.
- `/api/v1/markets` returns **403** (bot wall); market ids come from
  `orderBookDetails` instead.
- `markPriceCandles` exists with identical pagination (probed), but 90 d × 14 symbols
  would double the request count; the trade-close↔mark basis was **not** needed for
  the cross-venue study and was skipped to stay inside the rate budget.

## 2. Method (`validation/ou/ou_calibrate.py`)

`Δb_t = α + β·b_{t−1}` via OLS, `θ = −β/Δt`, `τ½ = ln2/θ`, Δt = 1 min (and 5 min resample).
- **CI**: Newey–West HAC on β (lag `4(n/100)^(2/9)`), mapped through τ½; plus
  stationary **block bootstrap** (block 240, 1000 draws, seed 7) percentile CI.
- **ADF**: constant, no trend, Schwert lag `⌈12(n/100)^{1/4}⌉` ≤ 20; MacKinnon 5% cv −2.863.
- **Variance ratio**: Lo–MacKinlay VR(q), q∈{5,15,60}, heteroskedastic-robust z.
- **Bands** follow `arbtwin/band.py` (floor 10, k=2, fee×1.5, cap 60, exit 0.5·entry)
  with fee proxy 7 bps (OKX taker 5 + Lighter 0; Lighter fees are 0 — verified in
  `markets_orderBookDetails.json`); **stop = 3σ√τ½** (repo invariant
  `adverse_sigma_mult`); **coupling**: stop↑ ⇒ exit↓ via `exit·0.9^(Δstop/σ)`,
  clamped to [floor/2, 0.5·entry]. Monotonicity tested (§5).

Estimator validation (synthetic): AR(1) φ=0.99 → τ½ 71.7 vs true 69; RW → VR≈1,
ADF non-stationary; φ∈{0.9,0.5} → ADF −23/−29. Commands in §5.

**Series kind caveat**: for the 12 single-venue symbols the "basis" is
`(close − EMA60)/EMA60` in bps (labelled `close-vs-ema60` in every table). This is a
mean-reverting *price deviation*, not a venue basis — it exists to exercise the full
pipeline and bound σ from above. The only real basis rows are the cross-venue
Lighter↔Binance ones (BTC/ETH, labelled `cross-venue`).

## 3. Results

### 3.1 Genuine cross-venue basis (Lighter mainnet ↔ Binance USDT-M perp, 1m)

| pair | n (aligned) | θ/min | τ½ (min) | τ½ CI (boot) | ADF t | VR(5) | VR(60) | σ (bps) | entry | exit | stop |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | 116,079 | 0.1113 | **6.2** | [3.5, 10.5] | −12.8 | 0.248 | 0.032 | 2.05 | 10.5 | 5.3 | 15.4 |
| ETH | 116,050 | 0.1179 | **5.9** | [3.8, 8.7] | −13.0 | 0.238 | 0.030 | 2.35 | 10.5 | 5.3 | 17.1 |

ADF rejects a unit root decisively; VR collapses monotonically toward 0 with q —
**this is a real, fast mean-reverting basis, not a sampling artefact** (a pure
random-walk basis would hold VR≈1; a spurious-regression artefact would not decay
this way).

Scale check on the same basis (`binance/cross_venue_scale.json`):

| scale | BTC τ½ | BTC CI | ETH τ½ | ETH CI |
|---|---:|---|---:|---|
| 1m | 6.2 | [3.5, 10.5] | 5.9 | [3.8, 8.7] |
| 5m | 37.3 | [22.3, 47.5] | 34.7 | [23.0, 43.1] |
| 60m | 227.4 | [105.4, 313.4] | 215.7 | [105.3, 309.6] |

**Reading**: τ½ is not scale-invariant here — the 1m basis is dominated by
microstructure noise that decays in minutes; the hourly basis carries a slower
premium component (~3.5–5 h). Both are present simultaneously (VR at 60m on 1m
data ≈ 0.03, i.e. variance keeps collapsing beyond the 1m half-life, so a second,
slower factor exists).

### 3.2 Single-venue deviation series (12 approved + BTC/ETH controls, `close-vs-ema60`, 1m)

From `results_1m.json` (command: `python validation/ou/ou_calibrate.py --resample 1 --json-out validation/ou/results_1m.json`):

| sym | n | θ/min | τ½ (min) | τ½ CI (boot) | ADF t | VR(5) | VR(60) | σ dev (bps) | entry | exit | stop |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| SOXL | 131,074 | 0.0336 | 21 | [17, 20] | −39.9 | 0.93 | 0.42 | 70.9 | 60.0 | 30.0 | 966.0 |
| COIN | 134,592 | 0.0317 | 22 | [18, 21] | −40.3 | 0.95 | 0.42 | 35.7 | 60.0 | 30.0 | 501.3 |
| ORCL | 130,457 | 0.0314 | 22 | [17, 23] | −42.5 | 0.96 | 0.48 | 34.6 | 60.0 | 30.0 | 487.7 |
| SNDK | 129,762 | 0.0328 | 21 | [17, 21] | −40.4 | 0.93 | 0.43 | 44.2 | 60.0 | 30.0 | 609.4 |
| LIT | 130,024 | 0.0355 | 20 | [16, 19] | −42.0 | 0.94 | 0.42 | 72.4 | 60.0 | 30.0 | 959.8 |
| MSTR | 129,693 | 0.0315 | 22 | [18, 21] | −39.1 | 0.97 | 0.43 | 41.5 | 60.0 | 30.0 | 583.8 |
| MRVL | 131,448 | 0.0318 | 22 | [17, 22] | −39.8 | 0.98 | 0.46 | 44.2 | 60.0 | 30.0 | 618.8 |
| VVV | 130,298 | 0.0347 | 20 | [16, 20] | −40.1 | 0.92 | 0.42 | 72.7 | 60.0 | 30.0 | 974.0 |
| XPT | 130,958 | 0.0317 | 22 | [18, 21] | −41.0 | 0.95 | 0.47 | 18.4 | 36.8 | 18.4 | 258.3 |
| GRAM | 131,169 | 0.0416 | 17 | [14, 17] | −44.1 | 0.92 | 0.37 | 34.5 | 60.0 | 30.0 | 422.8 |
| EWY | 133,147 | 0.0297 | 23 | [19, 22] | −41.1 | 1.01 | 0.49 | 25.0 | 50.0 | 25.0 | 361.9 |
| TRUMP | 130,727 | 0.0344 | 20 | [16, 20] | −40.4 | 0.92 | 0.38 | 69.9 | 60.0 | 30.0 | 940.7 |
| BTC (ctrl) | 130,023 | 0.0349 | 20 | [16, 19] | −39.9 | 0.91 | 0.41 | 19.2 | 38.3 | 19.2 | 256.0 |
| ETH (ctrl) | 130,022 | 0.0323 | 22 | [17, 21] | −39.9 | 0.93 | 0.44 | 26.9 | 53.9 | 26.9 | 374.2 |

All series: ADF −39…−44 (strongly stationary, as expected for a price-vs-EMA gap),
VR(5)≈1 decaying slowly — a *slowly* mean-reverting deviation dominated by the
60-min EMA horizon. 5m resample shifts τ½ to 18–24 min (`results_5m.json`),
i.e. these τ½ values track the EMA anchor, not the instrument: treat them as
machinery validation + σ upper bounds, **not** as venue-basis half-lives.

### 3.3 Comparison with the repo's hourly estimates

Repo (7-day hourly closes, `config/symbols.yaml` `basis_sigma_bps` / `band_bps`):

| sym | repo σ_basis (bps, 1h) | here σ dev (bps, 1m, EMA60) | repo band | here entry |
|---|---:|---:|---:|---:|
| SOXL | 6.87 | 70.9 | 13.7 | 60.0 |
| COIN | 4.99 | 35.7 | 10.0 | 60.0 |
| ORCL | 5.05 | 34.6 | 10.1 | 60.0 |
| SNDK | 4.36 | 44.2 | 10.0 | 60.0 |
| LIT | 6.36 | 72.4 | 12.7 | 60.0 |
| MSTR | 5.54 | 41.5 | 11.1 | 60.0 |
| MRVL | 4.87 | 44.2 | 10.0 | 60.0 |
| VVV | 5.99 | 72.7 | 12.0 | 60.0 |
| XPT | 5.42 | 18.4 | 10.8 | 36.8 |
| GRAM | 7.49 | 34.5 | 15.0 | 60.0 |
| EWY | 5.51 | 25.0 | 11.0 | 50.0 |
| TRUMP | 5.36 | 69.9 | 10.7 | 60.0 |

These are **not comparable quantities**: repo σ is a two-venue basis at hourly
closes; the 1m single-venue σ here is a price-vs-anchor deviation (price vol, not
basis vol). The only apples-to-apples comparison available is BTC/ETH
cross-venue: Lighter↔Binance 1m σ ≈ 2.1–2.4 bps vs repo Lighter↔OKX 1h
σ ≈ 5 bps (BTC, README §1.4) — consistent with the scale story: hourly sampling
admits slower, wider basis moves; at 1m the basis is tight and fast (τ½ ~6 min).
The repo's 0.8–1.7 h estimates sit between our 1m (6 min) and 60m (3.5–5 h)
cross-venue numbers, i.e. **hourly-close τ½ estimates are a blend of the fast
microstructure component and the slow premium component**; for entry/exit band
design the 1m numbers (τ½ minutes, σ ~2 bps) are the operationally relevant ones,
but they must be re-measured against OKX once reachable — Binance↔Lighter is a
different pair (no USDT/USDC settlement mismatch on Binance USDT-M? no — Binance
is also USDT-margined, so the stablecoin leg actually matches OKX's side; still
a different fee/latency profile).

## 4. OKX: exactly what is missing

1. **Network**: from this box `www.okx.com` → 169.254.0.2 (DNS poison), `api.okx.com`
   → no address (re-verified today). Everything OKX needs a **Linux VPS with clean
   DNS or an `ARBTWIN_PROXY`** (`export ARBTWIN_PROXY=http://host:port`).
2. **1m history**: with VPS, `GET /api/v5/market/history-candles?instId=<SYM>-USDT-SWAP&bar=1m&limit=100`
   paginated by `after` — 90 d × 14 symbols ≈ 130k rows × 14, straightforward.
   Lighter data already on disk covers the same window; alignment key = UTC ms
   open time (Lighter `t` == OKX `ts` bucket start, both aligned to minute).
3. **Static fallbacks checked, all fail for this window**:
   - Kaggle `thirtysevenhhhh/okx-15m-ohlcv-v2`: 2025-04→2026-05, **0 days overlap**
     (fetched `okx_BTC-USDT-SWAP_15m.parquet` as evidence; BTC/ETH/HMSTR only symbols
     of interest present, none of the 12 approved except via other files — none overlap).
   - Kaggle search "okx 1m", "okx perp ohlcv": no dataset covers 2026-08→11.
   - Tardis: API key not present (`.secrets/` has only kaggle/github tokens); Tardis
     is tick/L2 anyway, not needed for 1m OHLCV.
   - OKX's own historical-data page: domain DNS-poisoned, unreachable.
   - CryptoDataDownload: 1m OHLCV behind $80/mo API, BTC/ETH only, no RWA perps.
4. **What the table needs to be complete**: for each of the 12 symbols, the same
   `ou_calibrate.py` run with `b = (okx_close − lighter_close)/lighter_close × 1e4`
   on the aligned 1m grid (the cross-venue code path already exists and is proven
   on Binance — §5). Expected σ per README §1.4 is 5–13 bps at hourly scale; at 1m
   expect ~2–4 bps with τ½ in minutes, like Binance.

## 5. Reproduction commands (all run from `W:\dynamicTWIN`)

```bash
# 0. market map + endpoint probes
curl -sS "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails" \
    -o validation/ou/data/markets_orderBookDetails.json

# 1. Lighter 1m candles, 90 d (run in 3 batches; 429 backoff built in)
python validation/ou/fetch_lighter_candles.py --kind trade --days 90 --workers 2

# 2. Binance 1m static source (BTCUSDT, ETHUSDT; 2026-08..2026-10)
python - <<'PY'
import urllib.request, os
os.makedirs("validation/ou/data/binance", exist_ok=True)
base="https://data.binance.vision/data/futures/um/monthly/klines"
for sym in ("BTCUSDT","ETHUSDT"):
    for m in ("2026-08","2026-09","2026-10"):
        url=f"{base}/{sym}/1m/{sym}-1m-{m}.zip"
        dst=f"validation/ou/data/binance/{sym}-1m-{m}.zip"
        if not os.path.exists(dst):
            open(dst,"wb").write(urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"ou-calib/1.0"}),timeout=120).read())
PY
# (zip→parquet parse + cross-venue OU: see validation/ou/cross_venue.py)

# 3. OKX external-source test (Kaggle; proves 0 overlap)
python - <<'PY'
import urllib.request
tok=open('.secrets/kaggle_token').read().strip()
url="https://www.kaggle.com/api/v1/datasets/download/thirtysevenhhhh/okx-15m-ohlcv-v2/BTC_USDT_SWAP.parquet"
open("validation/ou/data/okx_BTC-USDT-SWAP_15m.parquet","wb").write(
    urllib.request.urlopen(urllib.request.Request(url,headers={"Authorization":f"Bearer {tok}"}),timeout=300).read())
PY

# 4. OU calibration (all 14, 1m + 5m)
python validation/ou/ou_calibrate.py --resample 1 --json-out validation/ou/results_1m.json
python validation/ou/ou_calibrate.py --resample 5 --json-out validation/ou/results_5m.json
# cross-venue (Binance) basis:
python validation/ou/cross_venue.py

# 5. estimator validation + band-coupling monotonicity
python - <<'PY'
import numpy as np, sys; sys.path.insert(0,"validation/ou")
from ou_calibrate import ou_ols, variance_ratio, adf_stat, suggest_bands
rng=np.random.default_rng(1); rw=np.cumsum(rng.standard_normal(50000))
print("RW VR:", variance_ratio(rw))                    # VR≈1, ADF non-stat
phi=0.99; x=np.empty(50000); x[0]=0; e=rng.standard_normal(50000)
for i in range(1,50000): x[i]=phi*x[i-1]+e[i]
ou=ou_ols(x,1.0); print("AR(.99) tau:", round(ou["half_life_min"],1))  # 71.7 vs true 69
ex=[suggest_bands(20.,t)["exit_bps"] for t in (1,5,20,60,240,1440)]
st=[suggest_bands(20.,t)["stop_bps"] for t in (1,5,20,60,240,1440)]
print("exit non-increasing:", all(a>=b for a,b in zip(ex,ex[1:])),
      "stop non-decreasing:", all(a<=b for a,b in zip(st,st[1:])))
PY
```

## 6. Caveats

- Binance↔Lighter basis uses **close-to-close** 1m values: it understates
  intra-minute dislocation and includes settlement-lag noise; it is *not* an
  OKX proxy for execution (fees: Binance taker 2.6–4.5 bps vs OKX 5; latency
  differs). No OKX–Lighter result is claimed anywhere in this file.
- σ on the 12 single-venue rows is a **price deviation**, not a basis; only the
  cross-venue rows (§3.1) are basis statistics.
- Lighter candles are trade-price OHLCV (`/candles`), not mark; mark-price
  candles exist but were skipped (rate budget) — irrelevant for the cross-venue
  basis, which uses both venues' trade closes.
- Bootstrap CIs are wide at 60m (n=1,935); the 1m/5m CIs are tight.
