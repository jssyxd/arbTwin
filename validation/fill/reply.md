# T2 fill-analysis — done

## Files
- `validation/fill/findings.md` — full report
- `validation/fill/data/_analysis_final.json` — raw JSON (same as stdout)
- `validation/fill/reply.md` — this file

## Changes
- `fill_model.py:1168-1169`: fixed `synthetic_capture` crash — added missing
  `book_venue_ts_ms`, `book_resync_age_s` arrays (were `np.zeros(0)`, now
  match `book_sym` length)
- `fill_model.py:914-953`: rewrote `QueueDepletionModel` docstring with
  exact conservative fill condition, queue-ahead source, and formula;
  default `use_cancel=False`
- `fill_model.py:984-1009`: updated `QueueMonteCarloModel` docstring,
  default `use_cancel=False`
- `analysis.py:28`: distances now (0,1,2,5,8,16,32)

## Verified
- `python validation/fill/analysis.py --data validation/fill/data/sample --skip-synthetic` → exit 0
- Full run with synthetic → exit 0; synthetic recovery shows analytic-vs-MC agreement (diff < 0.01)
- p_fill at touch (conservative): ETH 0.78@60s via price-through, 1.00 via queue;
  VVV 0.86 price-through, 0.94 MC; COIN 0.97 price-through; LIT 0.87 price-through.
  At 5 bps the models diverge: VVV 0.44 (p_thru) vs 0.35 (anal) vs 0.74 (MC).

## Limitations stated in findings.md
Lighter-only, 7 min smoke sample, not OKX, cannot set position size;
live calibration needs ≥14d L2, OKX data, execution log, time-of-day splits.