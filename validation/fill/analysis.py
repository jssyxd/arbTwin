"""Driver that turns a capture into the tables quoted in `findings.md`.

    python validation/fill/analysis.py --data validation/fill/data/sample \
        --symbols ETH,COIN,VVV,LIT --out validation/fill/data/analysis.json

Everything printed is also written to `--out` as JSON, so the report and the
numbers cannot drift apart.  Synthetic checks are run here too and are labelled
`source="synthetic"`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from validation.fill.fill_model import (Capture, DEFAULT_BUCKETS, MidPath, PriceThroughModel,  # noqa: E402
                                        QueueDepletionModel, QueueMonteCarloModel, bucket_label,
                                        calibrate, load_capture, order_flow, p_fill_table,
                                        sensitivity_table, synthetic_capture, touch_spells)

DISTANCES = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)


def fmt(x, nd=4):
    if x is None:
        return "-"
    if isinstance(x, float) and not np.isfinite(x):
        return "-"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def calibration_block(cap: Capture, symbol: str, side: str) -> dict:
    c = calibrate(cap, symbol, side)
    d = c.to_dict()
    print(f"\n=== calibration {symbol} {side} (source={cap.source}) ===")
    hdr = f"{'bucket':>16}{'n_lvl':>7}{'Q0_base':>10}{'mu_trade':>10}{'mu_cancel':>10}" \
          f"{'mu_place':>10}{'pi_thru':>9}{'drain/s':>9}{'life_s':>8}"
    print(hdr)
    n_lv = order_flow(cap, symbol, side)
    nlv = n_lv.n_levels_by_bucket if n_lv is not None else [0] * len(c.buckets)
    for i, lab in enumerate(c.buckets):
        print(f"{lab:>16}{int(nlv[i]):>7}{fmt(d['queue_base'][i], 3):>10}"
              f"{fmt(d['trade_rate_base_per_s'][i], 5):>10}{fmt(d['cancel_rate_base_per_s'][i], 5):>10}"
              f"{fmt(d['place_rate_base_per_s'][i], 5):>10}{fmt(d['through_hazard_per_s'][i], 5):>9}"
              f"{fmt(d['queue_drain_rate_per_s'][i], 5):>9}{fmt(d['level_lifetime_s'][i], 1):>8}")
    for note in c.notes:
        print(f"  note: {note}")
    return d


def synthetic_recovery(seed: int = 21) -> dict:
    """Calibrate a synthetic market with known rates; report the recovery error."""
    truth = dict(trade_intensity_per_s=0.8, trade_size_base=1.5, touch_size_base=10.0,
                 cancel_intensity_per_s=0.4, cancel_frac=0.5, half_spread_bps=1.0)
    cap = synthetic_capture(symbol="SYNTH", duration_s=1200.0, step_s=0.25, mid0=2000.0,
                            sigma_bps_per_sqrt_s=8.0, seed=seed, order_poll_s=2.0, **truth)
    out = {"source": "synthetic", "truth": truth, "sides": {}}
    print("\n=== synthetic recovery check (source=synthetic) ===")
    for side in ("buy", "sell"):
        c = calibrate(cap, "SYNTH", side)
        i0 = 0                                   # touch bucket
        spells = touch_spells(cap, "SYNTH", side)
        flow = order_flow(cap, "SYNTH", side)
        out["sides"][side] = {
            "touch_queue_base_measured": float(c.queue_base[i0]) if np.isfinite(c.queue_base[i0]) else None,
            "touch_trade_rate_measured": float(c.trade_rate_base_per_s[i0])
            if np.isfinite(c.trade_rate_base_per_s[i0]) else None,
            "touch_cancel_rate_measured": float(c.cancel_rate_base_per_s[i0])
            if np.isfinite(c.cancel_rate_base_per_s[i0]) else None,
            "n_touch_spells": len(spells),
            "n_order_polls": flow.n_polls if flow else 0,
            "buckets": c.to_dict(),
        }
        print(f"  {side}: Q0={fmt(out['sides'][side]['touch_queue_base_measured'], 3)} "
              f"mu_trade={fmt(out['sides'][side]['touch_trade_rate_measured'], 5)} "
              f"mu_cancel={fmt(out['sides'][side]['touch_cancel_rate_measured'], 5)} "
              f"spells={len(spells)} polls={out['sides'][side]['n_order_polls']}")
    # analytic vs MC agreement at the touch, same calibration
    c = calibrate(cap, "SYNTH", "buy")
    anal = QueueDepletionModel(calib=c)
    mc = QueueMonteCarloModel(calib=c, mid_path=MidPath.fit(cap, "SYNTH", "bootstrap"),
                              n_paths=1500, seed=5)
    agree = []
    for h in (60.0, 300.0):
        a = anal.p_fill("buy", 0.5, h)["p_fill"]
        m = mc.p_fill("buy", 0.5, h)["p_fill"]
        agree.append({"horizon_s": h, "analytic": a, "monte_carlo": m, "abs_diff": abs(a - m)})
        print(f"  h={h:.0f}s analytic={a:.3f} mc={m:.3f} diff={abs(a - m):.3f}")
    out["analytic_vs_mc"] = agree
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "data", "sample"))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--horizons", default="60,300")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "data", "analysis.json"))
    ap.add_argument("--n-paths", type=int, default=1200)
    ap.add_argument("--skip-synthetic", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    cap = load_capture(args.data)
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(cap.symbols)
    horizons = tuple(float(x) for x in args.horizons.split(","))
    desc = cap.describe()
    print(f"[analysis] capture={args.data}")
    print(json.dumps(desc, indent=2))

    out: dict = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "data_dir": args.data, "describe": desc, "calibrations": {},
                 "p_fill_tables": {}, "sensitivity": {}, "runtime_s": None}

    for sym in syms:
        if sym not in cap.symbol_ids:
            print(f"[analysis] skip {sym}: not in capture")
            continue
        for side in ("buy", "sell"):
            out["calibrations"][f"{sym}:{side}"] = calibration_block(cap, sym, side)
        rows = p_fill_table(cap, sym, "buy", DISTANCES, horizons_s=horizons, n_paths=args.n_paths)
        out["p_fill_tables"][sym] = rows
        print(f"\n=== p_fill {sym} buy (source={cap.source}) ===")
        print(f"{'d_bps':>7}{'h_s':>7}{'p_thru':>9}{'p_anal':>9}{'p_mc':>9}"
              f"{'Q0':>9}{'mu_tr':>9}{'mu_ca':>9}{'pi':>8}{'n':>6}")
        for r in rows:
            print(f"{r['distance_bps']:>7.1f}{r['horizon_s']:>7.0f}"
                  f"{fmt(r['p_fill_price_through'], 3):>9}{fmt(r['p_fill_queue_analytic'], 3):>9}"
                  f"{fmt(r['p_fill_queue_mc'], 3):>9}{fmt(r['queue_base'], 3):>9}"
                  f"{fmt(r['trade_rate'], 5):>9}{fmt(r['cancel_rate'], 5):>9}"
                  f"{fmt(r['through_hazard'], 5):>8}{str(r['n_starts']):>6}")
        sens = sensitivity_table(cap, sym, "buy", (0.0, 2.0, 8.0), horizon_s=max(horizons),
                                 n_paths=max(600, args.n_paths // 2))
        out["sensitivity"][sym] = sens
        print(f"\n=== sensitivity {sym} buy @h={max(horizons):.0f}s ===")
        print(f"{'variant':>14}{'mid':>11}{'qmult':>7}{'cancel':>8}{'d_bps':>7}{'p_fill':>9}{'E[T|f]':>9}")
        for r in sens:
            print(f"{r['variant']:>14}{r['mid_mode']:>11}{r['queue_mult']:>7.1f}"
                  f"{str(r['use_cancel']):>8}{r['distance_bps']:>7.1f}"
                  f"{fmt(r['p_fill'], 3):>9}{fmt(r['mean_fill_s'], 1):>9}")

    if not args.skip_synthetic:
        out["synthetic_recovery"] = synthetic_recovery()

    out["runtime_s"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[analysis] wrote {args.out} in {out['runtime_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
