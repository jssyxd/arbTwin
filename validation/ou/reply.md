# T1 reply — OU half-life & threshold calibration (reachable data only)

**Done** (all under `validation/ou/`, nothing in `arbtwin/` touched, no commit/push):

- `ou_calibrate.py` — OLS `Δb = α + β·b` with Newey–West CI, stationary block-bootstrap
  CI, ADF (Schwert lag), Lo–MacKinlay VR(q), band suggestions with stop↑⇒exit↓ coupling.
  Estimators validated on synthetic AR(1)/random-walk; band monotonicity tested.
- `fetch_lighter_candles.py` + 90 days × 14 symbols of Lighter 1m candles
  (129k–134k rows each, gap_frac 0.000). Endpoint quirks documented in the script
  docstring (`resolution=1m`, `start_timestamp≥1`, 500-row pages, 429 backoff).
- `cross_venue.py` + `data/binance/*` — the only real two-venue basis available:
  **Lighter↔Binance** (Vision monthly 1m, 82-day overlap, 116k aligned minutes).
  BTC τ½ 6.2 min (CI 3.5–10.5), ETH 5.9 min; σ 2.05/2.35 bps; ADF −12.8/−13.0;
  VR decays to 0.03 → genuine fast mean reversion, not sampling artefact.
- `findings.md` — data inventory, per-symbol tables (1m + 5m), scale comparison
  (1m/5m/60m), repo-hourly-estimate comparison, exact OKX prerequisites.
- `results_1m.json`, `results_5m.json` — machine-readable per-symbol output.

**OKX: not fetched.** DNS-poisoned (re-verified). Kaggle `okx-15m-ohlcv-v2` covers
2025-04→2026-05, **0-day overlap** with the Lighter window — saved as evidence
(`okx_BTC-USDT-SWAP_15m.parquet`), not used in any paired result. No OKX–Lighter
number is claimed anywhere. The 12 approved symbols' rows are single-venue
`close-vs-ema60` deviations (labelled as such), machinery + σ upper bounds only.

**Reproduce**: `python validation/ou/ou_calibrate.py --resample 1` ·
`python validation/ou/cross_venue.py` · full command list in `findings.md` §5.
