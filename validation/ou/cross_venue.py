"""Cross-venue OU calibration: Lighter mainnet vs Binance USDT-M perp 1m closes.

The only genuine two-venue basis measurable from this network today (OKX is
DNS-poisoned; Binance Vision static monthly klines are reachable and overlap the
Lighter window).  NOTE: this is a *Binance*↔Lighter basis, NOT OKX↔Lighter; it is
used as a machinery proof and a scale reference.  findings.md §3.1/§4 explains.

Reads:
    validation/ou/data/{BTC,ETH}_1m.json              (Lighter trade candles)
    validation/ou/data/binance/{BTCUSDT,ETHUSDT}_1m.parquet
Writes:
    validation/ou/data/binance/{BTC,ETH}_cross_venue_ou.json
    validation/ou/data/binance/cross_venue_scale.json   (5m/60m resample)

Usage:  python validation/ou/cross_venue.py
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys_dir = HERE
import sys

sys.path.insert(0, sys_dir)
from ou_calibrate import (adf_stat, block_bootstrap_tau, ou_ols,  # noqa: E402
                          suggest_bands, variance_ratio)

DATA = os.path.join(HERE, "data")
BIN = os.path.join(DATA, "binance")

PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def aligned_basis(sym: str, binance_sym: str) -> pd.DataFrame:
    d = json.load(open(os.path.join(DATA, f"{sym}_1m.json"), encoding="utf-8"))
    lighter = pd.DataFrame(
        {"t": [r["t"] for r in d["candles"]],
         "lc": [r["c"] for r in d["candles"]]})
    b = pd.read_parquet(os.path.join(BIN, f"{binance_sym}_1m.parquet"))
    m = lighter.merge(b, left_on="t", right_on="timestamp", how="inner")
    m = m.sort_values("t").reset_index(drop=True)
    m["basis_bps"] = (m["lc"] - m["close"]) / m["close"] * 1e4
    return m


def _result(basis: np.ndarray, dt_min: float, label: str) -> dict:
    ou = ou_ols(basis, dt_min)
    sig = float(basis.std(ddof=1))
    adf = adf_stat(basis)
    return {
        "symbol": label,
        "n": int(len(basis)),
        "theta_per_min": round(ou["theta_per_min"], 6),
        "tau_half_min": round(ou["half_life_min"], 2),
        "tau_ci_boot": [round(x, 2) for x in block_bootstrap_tau(basis, dt_min)],
        "adf_t": round(adf["adf_t"], 2),
        "adf_stationary": adf["stationary"],
        "vr": {str(q): [round(v[0], 3), round(v[1], 2)]
               for q, v in variance_ratio(basis).items()},
        "sigma_bps": round(sig, 3),
        "bands": suggest_bands(sig, ou["half_life_min"]),
    }


def resample_last(ts: np.ndarray, basis: np.ndarray, k_min: int):
    key = ts // (k_min * 60_000)
    keep = np.ones(len(key), dtype=bool)
    keep[:-1] = key[1:] != key[:-1]
    return basis[keep]


def main() -> None:
    for sym, bnp in PAIRS.items():
        m = aligned_basis(sym, bnp)
        basis = m["basis_bps"].to_numpy()
        ts = m["t"].to_numpy(dtype=np.int64)
        label = f"{sym} (Lighter mainnet vs Binance {bnp} perp)"
        res = _result(basis, 1.0, label)
        res["kind"] = "cross-venue basis, 1m close-to-close, NOT OKX"
        res["overlap_minutes"] = int(len(m))
        with open(os.path.join(BIN, f"{sym}_cross_venue_ou.json"), "w",
                  encoding="utf-8") as f:
            json.dump(res, f, indent=2)
        print(json.dumps(res, indent=2))

        scale = {}
        for k in (5, 60):
            bk = resample_last(ts, basis, k)
            scale[f"{sym}_{k}m"] = _result(bk, float(k), f"{sym}@{k}m")
        with open(os.path.join(BIN, "cross_venue_scale.json"), "w",
                  encoding="utf-8") as f:
            json.dump(scale, f, indent=2)


if __name__ == "__main__":
    main()
