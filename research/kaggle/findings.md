# Kaggle research — crypto pairs, spread, execution (for arbTwin)

Date: 2026-11-09. Method: Kaggle API (CLI 2.2.4, `KAGGLE_API_TOKEN` from `.secrets/kaggle_token`, KGAT token works without username) for dataset/kernel search + `kernels pull` for source inspection; web search for dataset cards. No datasets downloaded beyond two small notebooks. Timebox ~25 min.

## 1) Reusable items, ranked

1. **`powakadata/crypto-perpetual-funding-rates-and-open-interest`** (dataset) — funding rate + mark price + OI for the 8 largest Binance USDT perps, 8h interval, back to each contract's launch (BTC from Sep 2019). Small (76 KB). The only compact dataset that pairs funding with mark price, which is what a carry/basis sanity check needs. Cross-venue funding comparison arbTwin already does live (OKX 8h decimal vs Lighter 1h pct — repo invariant #2); this gives the long history for the Binance leg of a funding-differential screen.
2. **`jorijnsmit/binance-full-history`** (dataset, 28 GB) — 1-minute candles, ~1000+ pairs, parquet, per-pair files (verified file listing: `BTC-USDT.parquet`, `ADA-EUR.parquet` etc. — multi-quote pairs included). Right granularity for re-running arbTwin's screening funnel step 2 (7d hourly closes → ratio/CV/return-corr) at scale. Do not download; one pair's parquet is ~30 MB and the CLI `datasets files` listing paginates by token.
3. **`martinsn/high-frequency-crypto-limit-order-book-data`** (dataset, ~1 GB compressed; 1s/1min/5min aggregates) — 15-level LOB snapshots BTC/ETH/ADA from Coinbase WS, ~12 days (2021). Columns: midpoint, spread, 15 bid/ask levels. Usable to prototype fill-probability and depth-gate logic at 1s — but 2021 Coinbase only; calibrate width, not level. This is the only per-second cross-checkable book data that's small enough to actually pull.
4. **`qkxuuuu/cryptolob-2025`** (dataset, 2.2 GB) — deep LOB snapshots for BTC/ETH, calendar 2025, JSON/CSV, per-day files. Newer than martinsn; ties to the Ntakaris et al. benchmark lineage (FI-2010-style features). Unverified: usability rating is 0.375 and code count 0 — schema doc is thin [INFERENCE from card: "structured for time-series modeling"].
5. **`sebastienlorentz/hyperliquid-funding-positioning-and-sentiment`** (dataset, hourly) — price, funding, **basis, order-book depth**, OI, cross-venue funding and basis (Binance/Bybit vs Hyperliquid). Closest existing analogue to arbTwin's exact object of study (perp basis across venues, hourly). Caveat: 6 weeks of data at time of writing, updated hourly — good for schema ideas, not statistics. Also: `marvingozo/hyperliquid-l1-order-flow-microstructure-10-perps` (5 days, 6.8B rows, CC-BY-NC-4.0 — non-commercial license, cannot be used for live trading infra).
6. **`haozhu18/binance-funding-rate-history`** + starter notebook — full Binance funding-rate history incl. coin-margained (`cm`) symbols, parquet, e.g. `AAVEUSD_PERP` rows at 8h with rate ~1e-4. Useful as the reference implementation for *parsing* funding history; the notebook is only a load-and-plot.
7. **`phamtheanh97/statarb-pairs-bayesopt-kappa-halflife`** (kernel, 9 votes) — half-life via OU regression `Δspread = a + b·spread_lag`, `half_life = -ln(2)/b`, kappa = `-b`; then Bayesian optimization over (lookback, entry_threshold, stoploss_factor, half_life). The kappa/half-life code is textbook-correct and directly transplants to arbTwin `calibration.py` as an alternative/companion to percentile p95 entry.
8. **`addarm/pairs-trading-strategy-quant`** (kernel, 25 votes, most-voted pairs kernel on Kaggle) — rolling z-score `Z=(X-μ)/σ` on the price ratio, thresholds ±1σ moving with the window, rolling window so "no train/test split needed" (their claim; see pitfalls). Author's own honesty: ~100% paper return over 2 years with a 50% drawdown, no costs.
9. **`wptouxx/g-research-cointegration-analysis`** (kernel) — pairwise CADF (OLS residual + `ts.adfuller(resid, 1)`) across G-Research crypto assets, p-value "confusion matrix" heatmap. Finds "strong cointegration between ETH Classic and all other assets" — a classic spurious-cointegration artifact; useful as a negative example for arbTwin's `screening.py` (it needs the multiplicity guard it lacks).
10. **`sergionefedov/synthetic-limit-order-book-market-microstructure`** (dataset) — synthetic 10-day LOB, 3 regimes, Poisson arrivals, GBM + mean-reverting mid, OBI features. Not real data — but its generator is a legitimate harness for testing two-leg coordination logic (partial fills, re-pegging) without touching production.
11. **`krrdev1/binance-btcusdt-l3-market-microstructure-data`** (dataset) — non-aggregated trades, 100ms book diffs, 1000-level snapshots since Mar 2026 (ongoing). The right data for fill-simulation of the naked-leg unwind, but heavy and single-market.

Not ranked but noted: `adamatractor/dex-orderbook-data-5m` — 7-day free sample (12+ months paid) of Hyperliquid L2, 10-level depth + 47 features. Sales-gated; skip. G-Research crypto forecasting competition (closed 2019) lives on as a corpus of minute-level crypto notebooks — mostly dead for arbTwin (targets returns forecasting, not spread).

## 2) Table

| item | type | data | method | what to reuse | link |
|---|---|---|---|---|---|
| crypto-perpetual-funding-rates-and-open-interest | dataset | Binance USDT perps, 8h, 2019→ | — | long funding+mark history; carry sanity check | https://www.kaggle.com/datasets/powakadata/crypto-perpetual-funding-rates-and-open-interest |
| binance-full-history | dataset | 1m candles ~2000 pairs, 2017→2022 | — | re-run screening funnel at scale | https://www.kaggle.com/datasets/jorijnsmit/binance-full-history |
| high-frequency-crypto-limit-order-book-data | dataset | Coinbase LOB 1s/1m/5m, 15 levels, 12d 2021 | — | fill-prob / depth-gate prototyping at 1s | https://www.kaggle.com/datasets/martinsn/high-frequency-crypto-limit-order-book-data |
| CryptoLOB-2025 | dataset | BTC/ETH LOB 2025, daily files | — | modern per-second book stats | https://www.kaggle.com/datasets/qkxuuuu/cryptolob-2025 |
| hyperliquid-funding-positioning-and-sentiment | dataset | HL hourly: funding, basis, depth, cross-venue | — | schema analogue for OKX↔Lighter basis | https://www.kaggle.com/datasets/sebastienlorentz/hyperliquid-funding-positioning-and-sentiment |
| binance-funding-rate-history | dataset | Binance um+cm funding, full history | — | funding parser reference | https://www.kaggle.com/datasets/haozhu18/binance-funding-rate-history |
| statarb-pairs-bayesopt-kappa-halflife | kernel | yfinance stocks (equities!) | OU regression → κ, half-life; BayesOpt over params | half-life estimator for `calibration.py` | https://www.kaggle.com/code/phamtheanh97/statarb-pairs-bayesopt-kappa-halflife |
| pairs-trading-strategy-quant | kernel | stock/crypto ratios | rolling z-score ±1σ | rolling-percentile entry form; their drawdown honesty | https://www.kaggle.com/code/addarm/pairs-trading-strategy-quant |
| g-research-cointegration-analysis | kernel | G-Research minute crypto (post-2020) | pairwise CADF + heatmap | anti-pattern: no multiplicity correction | https://www.kaggle.com/code/wptouxx/g-research-cointegration-analysis |
| synthetic-limit-order-book-market-microstructure | dataset | synthetic 10d, 3 regimes | Poisson+GBM+OU generator | two-leg coordination test harness | https://www.kaggle.com/datasets/sergionefedov/synthetic-limit-order-book-market-microstructure |
| binance-btcusdt-l3-market-microstructure-data | dataset | BTCUSDT L3 100ms, since Mar 2026 | — | naked-leg unwind fill simulation | https://www.kaggle.com/datasets/krrdev1/binance-btcusdt-l3-market-microstructure-data |

## 3) Pitfalls found (with examples)

- **No transaction costs, anywhere.** Every top pairs kernel signals on raw z-cross. `addarm` states it explicitly: "We signaled a lot of trades in our simulation, that would create severe commission and execution costs." arbTwin `band.py` already floors the band at `fee_multiple * fee_bps` — validated by the entire Kaggle corpus lacking it.
- **Lookahead via full-sample z-score.** `artemburenok/pairs-trading-mean-reversion`: `df_zscore = (ratio - ratio.mean())/ratio.std()` over the whole sample, and `ratios_mavg5/20` rolling means computed without shift; entry compared to a threshold derived from future-inclusive statistics. Same family: `wptouxx` runs CADF on the full post-2020 window, then presents the same window's residuals as tradeable. arbTwin `backtest/replay.py` fill-at-`i+1` rule is stricter than anything on Kaggle here.
- **Spurious cointegration / no multiplicity control.** `wptouxx` tests all pairs, then reports "strong cointegration between eth classic and all other assets". With ~n² tests at p<0.05 you get many false pairs; ETC is the crypto that decoupled hardest in 2016 — exactly the pair the kernel would pick. `screening.py`'s hard calibration gate (ratio/CV/return-corr) is the right defense; do not add a cointegration screen without a Bonferroni/BH pass.
- **No half-life to size the exit window.** Only `phamtheanh97` computes it (OU regression). Everything else uses fixed lookbacks (5/20 bars in artemburenok), so exits either churn or get stuck. Not a lookahead issue — a sizing issue: arbTwin's p50-exit has no time dimension.
- **Paper returns without drawdown honesty.** addarm: ~100% 2-yr return *and* 50% max drawdown, no costs, no Sharpe disclosure in the source. Treat every Kaggle pairs "result" as an upper bound.
- **Timestamp/venue alignment.** No pulled kernel aligns clocks between sources; multi-venue Kaggle data (hyperliquid-funding-positioning) ships pre-aligned hourly — fine for stats, useless for entry timing at seconds. arbTwin's own WS-vs-REST split (invariant #1: `books5` vs 400-level REST depth) has no Kaggle analogue; nobody publishes both granularities.
- **Funding units.** Dataset cards confirm venue-dependent conventions: Binance `fundingRate` in decimal per 8h (e.g. `0.0001` = 1bp), Lighter percentage per 1h. Confirms invariant #2 from the field guide, now with a Kaggle-native example (haozhu18's `um`/`cm` files carry both margining conventions).

## 4) Mapping to arbTwin modules

- `calibration.py` — Replace/augment the p95 entry percentile with **half-life from the OU regression** (`phamtheanh97` code, transplants as ~10 lines: `Δspread ~ spread_lag` via `polyfit`, `half_life = -ln(2)/β`). Gives `calibration.py` a time axis: entry band that requires expected convergence before the naked-leg deadline, and a half-life-conditioned p50 exit. Calibration gate itself (ratio ≈ 1, CV, return-corr) needs nothing from Kaggle — it is already stricter than the corpus.
- `band.py` — Confirmed by omission: no Kaggle kernel prices fees into the band, so there is no alternative band formula to adopt. The σ→0 / σ→∞ degeneracy handling has no precedent in the corpus; keep. One adoptable idea: `artemburenok`'s 68/95 band-drawing is literally arbTwin's p50/p95 percentile pair expressed as σ multiples — equivalent, no change.
- `screening.py` — Anti-pattern import: `wptouxx` shows what happens without multiplicity control if a cointegration scan is ever added (false ETC-style pairs). If pair count grows, add BH-corrected p-values. Data: `jorijnsmit/binance-full-history` can re-run the funnel's 7d-hourly-ratio step offline for candidate symbols before spending API calls.
- `backtest/replay.py` — The one real gap Kaggle exposes: replay has "no queue position, no depth walk". `martinsn` (1s, 15 levels) is the only small dataset that supports building a queue-position/depth-walk fill model; `krrdev1` (100ms diffs, 1000 levels) is the production-grade version. Until then replay's fill-at-next-bar-close is honest for signal research, dishonest for leg-balance questions — keep them separated, as the docstring already does.
- `wave.py` / two-leg balance (the core problem) — No Kaggle kernel models asynchronous two-leg fills; nothing to import directly [this is a corpus gap, not a search failure — checked "naked leg", "asynchronous fill", "legging", "queue position" queries, zero relevant hits]. The two adjacent tools: `sergionefedov`'s synthetic LOB generator (build a synthetic two-venue book pair with correlated mid and regime-dependent fill intensity to test batch entry, top-up pricing off filled-leg average, and IOC escalation — without production risk) and `powakadata` funding history (to decide whether a frozen-price exit should ever be preferred over IOC when funding differentials are adverse).
- Funding differential screen — `powakadata` (Binance 8h) + `sebastienlorentz` (Hyperliquid hourly vs Binance/Bybit) give the schema for a cross-venue funding-basis table; the arbTwin unit-mismatch invariant is the critical join rule.

## 5) Dead ends

- **`kaggle datasets list --search "funding rate"`** — returns loan/world-news datasets; the search index tokenizes "funding" for venture funding. Use `--search "perpetual"` or the exact slug.
- **`kaggle kernels list --sort-by votes`** — invalid; must be `voteCount`. Cost me one round-trip.
- **`kaggle kernels view` / `GET /api/v1/kernels/view`** — 403/HTML for these kernels; metadata endpoint doesn't exist for public kernels. `kernels pull` works and is the only reliable source-inspection path.
- **`krupalpatel07/statistical-arbitrage-engine-using-cointegration`** — 403 on pull (private/deleted or restricted).
- **G-Research competition notebooks** — forecasting-oriented; nothing on execution.
- **Polymarket 100ms datasets** (several, high votes) — prediction-market CLOB, not perp books; skip unless arbTwin ever hedges with event markets.
- **`ironflow/hyperliquid-liquidations`** (289 MB, updated 2026-11-07) — liquidation events, wrong object for leg balance. Same for `ollibolli/btc-historical-leverage-liquidations-order-data`.

## 6) Surprises

- The KGAT token works via `KAGGLE_API_TOKEN` env var with no username — despite CLI 2.2.4 being "outdated" (2.2.5 exists). `KAGGLE_USERNAME` never needed.
- **No Kaggle kernel addresses the user's exact problem** — two-leg asynchronous execution with top-ups priced off the filled leg's average. The stat-arb corpus stops at signal generation; execution corpora are single-market. This validates that the answer has to be built (synthetic harness) rather than found.
- **`funding-carry-audit` (GitHub, surfaced in search)**: an audit of 883 perp series, 1.49M funding observations, 4 venues found *four look-ahead bugs in its own harness* and reversed its own conclusion twice, ending at null. Not Kaggle, but the strongest independent evidence that arbTwin's fill-at-`i+1` discipline and frozen-price-exit caution are the right paranoia level.
- Kaggle's crypto LOB data is **dominated by Polymarket and Hyperliquid** in 2026; CEX-venue L2 (OKX/Lighter equivalents) is essentially absent — Lighter will never be in a public dataset; OKX-API capture remains the only path.
- The most-voted "crypto arbitrage" kernels are about market efficiency tests and adjacency matrices (triangle arbitrage), not cross-venue spread capture — the topic is thinner on Kaggle than the field guide's framing suggests.

## Verification

- All dataset cards fetched via authenticated `GET /api/v1/datasets/view/<owner>/<slug>` (fields: title/subtitle/description/lastUpdated/votes/downloads).
- All 5 kernel method claims read from pulled `.ipynb` sources (`kaggle kernels pull`), not from blurbs.
- File listings for `jorijnsmit/binance-full-history` and `martinsn` confirmed via `kaggle datasets files`.
- Not downloaded: any large dataset (rule 4). `pham`/`addarm`/`wptouxx`/`sathyanarayanrao89`/`haozhu18`/`artemburenok` notebooks pulled to /tmp only (not persisted under research/).
