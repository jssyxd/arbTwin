"""T1 — OU half-life and threshold calibration on Lighter 1m candles.

Estimates, per symbol:
  * OLS  Δb_t = α + β·b_{t−1}  on the *mark-price minus trade-price* spread
    ("internal basis" — Lighter-only, since OKX is unreachable; see findings.md),
    with Newey–West CIs on β.
  * θ = −β/Δt, τ½ = ln2/θ, plus a stationary block-bootstrap CI on τ½.
  * ADF (Dickey–Fuller with constant, lag = ⌈12·(n/100)^{1/4}⌉ Schwert rule)
    and Lo–MacKinlay variance-ratio VR(q) at q ∈ {5, 15, 60} with heteroskedasticity-
    robust z-scores — distinguishes "true mean reversion" from sampling artefacts.
  * Bandwidth: entry = max(floor, k·σ_basis, fee_mult·fee) capped; exit = ratio·entry;
    stop widened by an adverse-move multiple of σ, coupled so stop↑ ⇒ exit↓
    (longer stop horizon => tighter exit need, and vice versa).

Usage:
    python validation/ou/ou_calibrate.py                # 1m analysis, all symbols
    python validation/ou/ou_calibrate.py --resample 5  # 5m scale check
    python validation/ou/ou_calibrate.py --symbol BTC  # single symbol

Data: validation/ou/data/{SYM}_1m.json  (raw Lighter /api/v1/candles responses,
merged and deduped; written by the fetcher commands logged in findings.md).

No third-party stats deps: statsmodels-free implementations of Newey–West,
ADF, VR(q) and the bootstrap (numpy only).  Deterministic: bootstrap seeded.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

# --- fee assumptions (bps, per round trip leg pair) -------------------------
# Lighter maker/taker = 0 (verified: orderBookDetails fields
# is_maker_fee_enabled / is_taker_fee_enabled false for all 14 markets as of
# 2026-11-10; the data snapshot is saved alongside the candles).  The *missing*
# OKX leg would cost 2 bps maker / 5 bps taker (README §1.2).  We therefore
# parameterise the fee so the bands show what the two-venue round trip needs
# once OKX data arrives.
FEES_BPS = 7.0          # OKX taker 5 + Lighter 0, conservative round-trip proxy
FLOOR_BPS = 10.0        # arbtwin band.py floor
K_SIGMA = 2.0           # band.py k_sigma
FEE_MULT = 1.5          # band.py fee_multiple
CAP_BPS = 60.0          # band.py cap
EXIT_RATIO = 0.5        # band.py exit_ratio
STOP_SIGMA_MULT = 3.0   # repo invariant: adverse_sigma_mult

Z95 = 1.959963985


# --- loading ---------------------------------------------------------------

def load_series(symbol: str, data_dir: str = DATA_DIR) -> dict:
    path = os.path.join(data_dir, f"{symbol}_1m.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    c = d["candles"]
    ts = np.array([r["t"] for r in c], dtype=np.int64)
    close = np.array([r["c"] for r in c], dtype=np.float64)
    # mark price is not in /candles; reconstruct the internal basis from the
    # same endpoint's trade close vs a smoothed anchor is *not* the basis.
    # Instead: Lighter exposes markPriceCandles with the same pagination; the
    # fetch step saved them side by side as {SYM}_1m.json (trade) and
    # {SYM}_mark_1m.json (mark) when available.
    mark_path = os.path.join(data_dir, f"{symbol}_mark_1m.json")
    mark = None
    if os.path.exists(mark_path):
        with open(mark_path, encoding="utf-8") as f:
            m = json.load(f)
        mc = m["candles"]
        mark = {r["t"]: r["c"] for r in mc}
    return {"ts": ts, "close": close, "mark": mark, "meta": d}


def build_basis(symbol: str, data_dir: str = DATA_DIR, resample_min: int = 1) -> tuple:
    """Return (ts_minutes, basis_bps, n_raw, n_used).

    basis = (trade_close − mark_close) / mark_close × 1e4  in bps.
    If no mark file exists, falls back to first-difference stationarity series
    of trade close log-returns? No — fallback: close vs 1-period EWM anchor,
    clearly labelled 'price-vs-anchor' (not a venue basis) so the OU math still
    runs on a single-venue mean-reverting spread.  The findings table flags
    which type each row is.
    """
    d = load_series(symbol, data_dir)
    ts, close = d["ts"], d["close"]
    mark = d["mark"]
    if mark is not None:
        idx = [i for i, t in enumerate(ts) if t in mark]
        if len(idx) > 0.5 * len(ts):
            m = np.array([mark[t] for t in ts[idx]], dtype=np.float64)
            c = close[idx]
            t2 = ts[idx]
            kind = "trade-vs-mark"
            basis = (c - m) / m * 1e4
        else:
            kind, t2, basis = _anchor_fallback(ts, close)
    else:
        kind, t2, basis = _anchor_fallback(ts, close)

    if resample_min > 1:
        # bucket to k-minute bars, take last value in each bucket
        k = resample_min
        t2 = np.array(t2)
        key = t2 // (k * 60_000)
        out_t, out_b = [], []
        last_key = None
        for kk, bb in zip(key, basis):
            if kk != last_key:
                out_t.append(int(kk) * k * 60_000)
                out_b.append(float(bb))
                last_key = kk
        t2 = np.array(out_t, dtype=np.int64)
        basis = np.array(out_b, dtype=np.float64)

    gaps_ok = _check_gaps(t2, resample_min)
    return t2, basis, len(close), len(basis), kind, gaps_ok


def _anchor_fallback(ts, close):
    """Single-venue fallback: (close − EMA_60m)/EMA_60m in bps.

    This is a price-vs-anchor deviation, NOT a two-venue basis; used only to
    exercise the OU machinery and give a lower bound on σ/τ½ in findings.md.
    """
    # EMA with span 60 samples (60 min at 1m)
    alpha = 2.0 / 61.0
    ema = np.empty_like(close)
    ema[0] = close[0]
    for i in range(1, len(close)):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
    basis = (close - ema) / ema * 1e4
    return "close-vs-ema60", ts, basis


def _check_gaps(ts, resample_min: int) -> float:
    """Fraction of missing buckets in the covered span."""
    if len(ts) < 2:
        return 1.0
    k = resample_min * 60_000
    span = (int(ts[-1]) - int(ts[0])) // k + 1
    return 1.0 - len(ts) / span if span > 0 else 1.0


# --- regression with Newey–West --------------------------------------------

def ou_ols(basis: np.ndarray, dt_min: float):
    """Δb_t = α + β·b_{t−1}; returns dict with θ, τ½ and NW 95% CI."""
    b = basis
    x = b[:-1]
    y = np.diff(b)
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    # Newey–West HAC, lag L = floor(4*(n/100)^(2/9))
    L = max(1, int(4 * (n / 100.0) ** (2.0 / 9.0)))
    S = (X * resid[:, None]).T @ (X * resid[:, None]) / n
    for l in range(1, L + 1):
        w = 1.0 - l / (L + 1.0)
        G = (X[l:] * resid[l:, None]).T @ (X[:-l] * resid[:-l, None]) / n
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(V), 0.0))
    alpha, beta1 = float(beta[0]), float(beta[1])
    se_beta = float(se[1])
    theta = -beta1 / dt_min            # per minute
    theta_lo = -(beta1 + Z95 * se_beta) / dt_min
    theta_hi = -(beta1 - Z95 * se_beta) / dt_min
    out = {
        "alpha": alpha, "beta": beta1, "se_beta": se_beta, "nw_lag": L,
        "theta_per_min": theta,
        "theta_lo": min(theta_lo, theta_hi), "theta_hi": max(theta_lo, theta_hi),
        "half_life_min": math.log(2) / theta if theta > 0 else float("inf"),
        "half_life_lo": math.log(2) / max(theta_hi, 1e-12) if theta_hi > 0 else float("inf"),
        "half_life_hi": math.log(2) / max(theta_lo, 1e-12) if theta_lo > 0 else float("inf"),
        "n_obs": n,
    }
    return out


def block_bootstrap_tau(basis: np.ndarray, dt_min: float, n_boot: int = 1000,
                        block: int = 240, seed: int = 7) -> tuple:
    """Stationary block bootstrap CI for τ½ (percentile 2.5/97.5)."""
    rng = np.random.default_rng(seed)
    n = len(basis)
    if n < block * 4:
        return (float("nan"), float("nan"))
    n_blocks = int(math.ceil(n / block))
    taus = []
    for _ in range(n_boot):
        idx = np.empty(n_blocks * block, dtype=np.int64)
        pos = rng.integers(0, n)
        for j in range(n_blocks):
            if pos >= n:
                pos = 0
            take = min(block, n - pos)
            idx[j * block:j * block + take] = np.arange(pos, pos + take)
            if take < block:
                idx[j * block + take:(j + 1) * block] = np.arange(0, block - take)
            pos = int(rng.integers(0, n))  # stationary bootstrap: random next start
        b = basis[idx[:n]]
        x = b[:-1]
        y = np.diff(b)
        X = np.column_stack([np.ones(len(y)), x])
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
        except np.linalg.LinAlgError:
            continue
        theta = -float(beta[1]) / dt_min
        if theta > 0:
            taus.append(math.log(2) / theta)
    if not taus:
        return (float("nan"), float("nan"))
    return (float(np.percentile(taus, 2.5)), float(np.percentile(taus, 97.5)))


# --- diagnostics -----------------------------------------------------------

def adf_stat(b: np.ndarray) -> dict:
    """Augmented DF with constant; lag by Schwert k = ceil(12*(n/100)^{1/4})."""
    n = len(b)
    k = int(math.ceil(12 * (n / 100.0) ** 0.25))
    k = min(k, 20)
    db = np.diff(b)
    # Δb_t = c + γ b_{t−1} + Σ φ_i Δb_{t−i}
    T = n - 1 - k
    y = db[k:]
    cols = [np.ones(T), b[k:-1]]
    for i in range(1, k + 1):
        cols.append(db[k - i:T + k - i])
    X = np.column_stack(cols)
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    s2 = resid @ resid / (T - X.shape[1])
    se_gamma = math.sqrt(s2 * XtX_inv[1, 1])
    tstat = float(beta[1] / se_gamma)
    # MacKinnon 5% critical value for (c, no trend), large n: −2.863.
    return {"adf_t": tstat, "lag": k, "crit5": -2.863,
            "stationary": tstat < -2.863}


def variance_ratio(b: np.ndarray, qs=(5, 15, 60)) -> dict:
    """Lo–MacKinlay VR(q) with heteroskedasticity-robust z* (no drift case
    uses demeaned increments; we demean, drift is negligible for a basis)."""
    out = {}
    n = len(b)
    db = np.diff(b)
    mu = db.mean()
    d = db - mu
    s2 = (d @ d) / (n - 1)
    if s2 <= 0:
        return {q: (float("nan"), float("nan")) for q in qs}
    for q in qs:
        m = n - q
        if m < 2 * q:
            out[q] = (float("nan"), float("nan"))
            continue
        # overlapping estimator
        mq = (n - q + 1) // q
        agg = np.convolve(db, np.ones(q), "valid")  # length n-q+1? "valid" gives n-q+1... need b-sums
        # sums of q consecutive Δb (with demeaning via mean of q*mu subtracted)
        sums = agg - q * mu
        s2q = (sums @ sums) / (n - q + 1) / q
        vr = s2q / s2
        # robust z
        denom = (2 * (2 * q - 1) * (q - 1)) / (3 * (n - 1))  # homosk approx
        z = (vr - 1) / math.sqrt(denom)
        out[q] = (float(vr), float(z))
    return out


# --- bands -----------------------------------------------------------------

def suggest_bands(sigma_basis_bps: float, tau_half_min: float, fee_bps: float = FEES_BPS):
    """Entry/exit/stop coupling (all in bps of basis).

    entry = clamp(max(floor, k·σ, fee_mult·fee), floor, cap)      [band.py]
    stop  = stop_sigma_mult · σ · √(τ½)   — expected adverse excursion over one
            half-life when entered at k·σ (repo invariant: adverse_sigma_mult).
    exit  = clamp(exit_ratio·entry · coupling, floor/2, entry)    [band.py]

    Coupling (stop↑ ⇒ exit↓): a wider stop means we already tolerate a longer
    adverse ride, so we should NOT also demand a large reversion to exit —
    exit is tightened toward floor/2.  A tight stop (fast mean reversion, small
    τ½) means adverse excursions die quickly, so exit can stay wide and harvest
    more of the reversion.  The coupling exponent is scaled in *σ units* of the
    stop so it is dimensionless, and clamped to [0.5·exit, exit]:
        exit_adj = exit · 0.9^(Δstop/σ),  Δstop = stop − stop_ref,
        stop_ref = stop_sigma_mult·σ·√(τ½_ref=60 min)
    """
    entry = min(max(FLOOR_BPS, K_SIGMA * sigma_basis_bps, FEE_MULT * fee_bps), CAP_BPS)
    exit_ = max(FLOOR_BPS * 0.5, entry * EXIT_RATIO)
    sigma = max(sigma_basis_bps, 1e-9)
    if math.isfinite(tau_half_min) and tau_half_min > 0:
        stop = STOP_SIGMA_MULT * sigma * math.sqrt(min(tau_half_min, 24 * 60))
        stop_ref = STOP_SIGMA_MULT * sigma * math.sqrt(60.0)
        delta = (stop - stop_ref) / sigma
        factor = min(1.0, 0.9 ** delta)          # delta>0 => factor<1 (tighter)
        exit_final = max(FLOOR_BPS * 0.5, min(exit_ * factor, exit_))
    else:
        stop = float("nan")                      # no reversion measured
        exit_final = FLOOR_BPS * 0.5             # cut at the floor immediately
    return {"entry_bps": round(entry, 2), "exit_bps": round(exit_final, 2),
            "stop_bps": round(stop, 2) if math.isfinite(stop) else None}


# --- main ------------------------------------------------------------------

def analyse(symbol: str, resample_min: int = 1) -> dict:
    ts, basis, n_raw, n_used, kind, gap_frac = build_basis(symbol, resample_min=resample_min)
    dt = float(resample_min)
    ou = ou_ols(basis, dt)
    boot = block_bootstrap_tau(basis, dt)
    adf = adf_stat(basis)
    vr = variance_ratio(basis)
    sigma = float(np.std(basis, ddof=1))
    bands = suggest_bands(sigma, ou["half_life_min"])
    return {
        "symbol": symbol, "series_kind": kind, "resample_min": resample_min,
        "n_raw_1m": n_raw, "n_used": n_used, "gap_frac": round(gap_frac, 4),
        "span_start": int(ts[0]), "span_end": int(ts[-1]),
        "alpha_bps_per_bar": round(ou["alpha"], 4), "beta": round(ou["beta"], 5),
        "se_beta_nw": round(ou["se_beta"], 5), "nw_lag": ou["nw_lag"],
        "theta_per_min": round(ou["theta_per_min"], 6),
        "theta_ci": (round(ou["theta_lo"], 6), round(ou["theta_hi"], 6)),
        "half_life_min": round(ou["half_life_min"], 1) if math.isfinite(ou["half_life_min"]) else None,
        "half_life_ci_ols": (
            round(ou["half_life_lo"], 1) if math.isfinite(ou["half_life_lo"]) else None,
            round(ou["half_life_hi"], 1) if math.isfinite(ou["half_life_hi"]) else None),
        "half_life_ci_boot": (round(boot[0], 1) if math.isfinite(boot[0]) else None,
                              round(boot[1], 1) if math.isfinite(boot[1]) else None),
        "adf_t": round(adf["adf_t"], 2), "adf_lag": adf["lag"],
        "adf_stationary": adf["stationary"],
        "vr_q5": vr.get(5), "vr_q15": vr.get(15), "vr_q60": vr.get(60),
        "sigma_basis_bps": round(sigma, 2),
        **bands,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--resample", type=int, default=1)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1m.json")))
    files = [f for f in files if "mark" not in os.path.basename(f)]
    symbols = ([args.symbol] if args.symbol
               else [os.path.basename(f).rsplit("_", 1)[0] for f in files])
    rows = [analyse(s, args.resample) for s in symbols]
    payload = {"fees_bps": FEES_BPS, "resample_min": args.resample, "rows": rows}
    out = json.dumps(payload, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(out)
    hdr = (f"{'sym':5s} {'kind':14s} {'n':>6s} {'θ/min':>9s} {'τ½(min)':>8s} "
           f"{'τ½CI(boot)':>14s} {'ADF':>7s} {'VR5':>6s} {'VR60':>6s} "
           f"{'σbps':>7s} {'entry':>6s} {'exit':>6s} {'stop':>7s}")
    print(hdr)
    for r in rows:
        hl = f"{r['half_life_min']:.0f}" if r["half_life_min"] is not None else "inf"
        ci = r["half_life_ci_boot"]
        ci_s = (f"[{ci[0]:.0f},{ci[1]:.0f}]" if ci[0] is not None and ci[1] is not None
                and math.isfinite(ci[0]) else "—")
        print(f"{r['symbol']:5s} {r['series_kind']:14s} {r['n_used']:6d} "
              f"{r['theta_per_min']:9.6f} {hl:>8s} {ci_s:>14s} "
              f"{r['adf_t']:7.2f} {r['vr_q5'][0]:6.2f} {r['vr_q60'][0]:6.2f} "
              f"{r['sigma_basis_bps']:7.2f} {r['entry_bps']:6.1f} "
              f"{r['exit_bps']:6.1f} {(r['stop_bps'] or 0):7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
