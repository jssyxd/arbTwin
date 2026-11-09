# Kaggle research complete — `research/kaggle/`

**Top reusable:** `powakadata` funding+mark (Binance perps, 2019→, 76 KB) for carry sanity; `phamtheanh97` kernel's OU half-life estimator (`-ln(2)/β`, ~10 lines) as a time-axis upgrade for `calibration.py`; `martinsn` 1s/15-level LOB as the only small fill-model dataset; `sergionefedov` synthetic LOB as a two-leg coordination test harness.

**Key gaps validated:** no Kaggle kernel models asynchronous two-leg fills or top-up pricing — the corpus stops at signals, single-market execution only. arbTwin's fill-at-`i+1` and fee-floor band are stricter than the whole corpus (most-voted pairs kernel: full-sample z-score lookahead, no costs, 100% paper return with 50% drawdown). `wptouxx`'s "ETC cointegrates with everything" is the anti-pattern if a cointegration screen is ever added.

**Also found:** funding-carry-audit (GitHub) found 4 look-ahead bugs in its own 1.49M-obs harness and still concluded null — external validation of arbTwin's paranoia level.

Deliverables: `research/kaggle/findings.md`, `research/kaggle/sources.jsonl` (21 sources, method claims verified from pulled notebook sources).
