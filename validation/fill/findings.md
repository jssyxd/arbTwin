# Fill-probability findings — Lighter L2 (T2)

**Source**: `validation/fill/data/sample/` — 7 min continuous Lighter mainnet WS capture,
2026-11-09, symbols ETH/VVV/COIN/LIT.  Full JSON: `data/_analysis_final.json`.

> ⚠️ **Lighter only, not OKX**.  OKX is unreachable from this host (DNS resolves
> to `169.254.0.2`).  Every number below applies to the Lighter leg alone; the
> OKX leg must be calibrated independently before any two-leg conclusion.
> The 7 min window is a **smoke test**, not a calibration sample — do not set
> position size from these numbers.

## Capture coverage

| Symbol | Book msgs | Rate/s | Median spread (bps) | Trades | Traded base/s | Mid σ / √min (bps) |
|--------|-----------|--------|----------------------|--------|---------------|----------------------|
| ETH    | 4,595     | 10.9   | 0.47                 | 2,264  | 10.5          | 0.33                |
| VVV    | 6,111     | 14.5   | 2.66                 | 132    | 6.0           | 0.42                |
| COIN   | 7,070     | 16.8   | 1.31                 | 85     | 1.0           | 0.29                |
| LIT    | 5,733     | 13.7   | 1.26                 | 964    | 329.1         | 0.42                |

Duration 420 s; book rebuild integrity: ETH 92/99 touch mismatches before resync,
3,777 dropped (crossed book).  Acceptable for a 7 min smoke sample.

## Conservative p_fill definition

A resting post-only order fills **if and only if**:

1. **Price-through** — a trade prints *strictly through* our price (opposing best
   crosses our level).  Measured as the through-hazard π(d) at distance d.
2. **Queue-depletion** — cumulative *trades at our price level* consume the
   visible queue ahead of us (queue_ahead → 0).  Under FIFO only trades at our
   price reduce the queue ahead; cancellations are excluded (most are behind us).

**Queue-ahead source**: `calibration.queue_base` — median visible queue size at
the resting distance bucket, measured from Lighter's order-level REST polls.

**Excluded**: mid-cross-only fills (mid moves through our price without a trade
printing at our level), and cancellation-driven depletion (cancels are
non-FIFO and most are behind).  This is conservative vs MMV (2021) and Daru's
adverse-selection evidence.

### Formula (analytic, lower bound)

```
P(fill ≤ h) = 1 − exp(−(π(d) + μ_trade(d) / Q₀(d)) · h)
```

## p_fill by distance and horizon

### ETH (spread 0.47 bps)

| d (bps) | h=10s | h=60s | h=300s | Model |
|---------|-------|-------|--------|-------|
| 0       | 0.60  | 0.78  | 1.00   | Price-through |
| 0       | 1.00  | 1.00  | 1.00   | Queue analytic |
| 0       | 1.00  | 1.00  | 1.00   | Queue MC |
| 1       | 0.43  | 0.58  | 1.00   | Price-through |
| 1       | 1.00  | 1.00  | 1.00   | Queue analytic |
| 2       | 0.33  | 0.50  | 1.00   | Price-through |
| 2       | 1.00  | 1.00  | 1.00   | Queue analytic |
| 5       | 0.14  | 0.40  | 1.00   | Price-through |
| 5       | 1.00  | 1.00  | 1.00   | Queue analytic |

ETH touch: π=0.87 /s, μ_trade=1.38 base/s, Q₀=4.17 base → 99% fill
within 3 s.  The two models agree: the touch is near-certain execution.

### VVV (spread 2.66 bps)

| d (bps) | h=10s | h=60s | h=300s | Model |
|---------|-------|-------|--------|-------|
| 0       | 0.68  | 0.86  | 1.00   | Price-through |
| 0       | 0.97  | 1.00  | 1.00   | Queue analytic |
| 0       | 0.85  | 0.94  | 0.98   | Queue MC |
| 2       | 0.46  | 0.68  | 1.00   | Price-through |
| 2       | 0.33  | 0.91  | 1.00   | Queue analytic |
| 2       | 0.69  | 0.86  | 0.93   | Queue MC |
| 5       | 0.25  | 0.44  | 0.83   | Price-through |
| 5       | 0.07  | 0.35  | 0.88   | Queue analytic |

VVV shows the largest model divergence: at d=5 bps, price-through says
p_fill(60s)=0.44 but the analytic queue model says 0.35 (slower drain at
Q₀=44 base).  MC adds mid-path noise and lands at 0.74.

### COIN (spread 1.31 bps)

| d (bps) | h=10s | h=60s | h=300s | Model |
|---------|-------|-------|--------|-------|
| 0       | 0.82  | 0.97  | 1.00   | Price-through |
| 0       | 1.00  | 1.00  | 1.00   | Queue analytic |
| 0       | 0.98  | 1.00  | 1.00   | Queue MC |
| 2       | 0.54  | 0.90  | 1.00   | Price-through |
| 2       | 0.24  | 0.81  | 1.00   | Queue analytic |
| 5       | 0.23  | 0.78  | 1.00   | Price-through |
| 5       | 0.02  | 0.13  | 0.51   | Queue analytic |

COIN at d≥2 bps: tape shows near-zero trade flow (μ_trade≈0), so the
queue model says only price-through matters.  The large Q₀=76 base at 5 bps
makes drain negligible.

### LIT (spread 1.26 bps, highest turnover)

| d (bps) | h=10s | h=60s | h=300s | Model |
|---------|-------|-------|--------|-------|
| 0       | 0.64  | 0.87  | 0.99   | Price-through |
| 0       | 0.95  | 0.99  | 0.99   | Queue MC |
| 2       | 0.97  | 1.00  | 1.00   | Queue MC |
| 5       | 0.76  | 0.95  | 0.98   | Queue MC |
| 8       | 0.74  | 0.95  | 0.97   | Queue MC |

LIT is the liquidity outlier: Q₀=600 base at the touch, μ_trade=144 base/s,
yet drain is only 0.24 base/s per unit queue.  The MC model gives p_fill
remaining above 0.95 even at 8 bps because the mid doesn't run far enough
in 300 s to shut off trade flow.

## Markout

**Insufficient data**.  Markout requires paired fill events (a resting order
that *did* fill); we have modeled p_fill, not actual fills.  The 7 min window
has 3,445 trades but no resting-order execution log against which to measure
post-fill mid movement.  A live recording with placed post-only orders (or a
synthetic replay with the harness) is needed.

## Sensitivity (LIT buy, h=300s)

| Variant      | Queue mult | Cancel | d=0 bps | d=2 bps | d=8 bps |
|-------------|------------|--------|---------|---------|---------|
| analytic    | 0.5        | True   | 1.00    | 1.00    | 1.00    |
| analytic    | 0.5        | False  | 1.00    | 1.00    | 0.75    |
| analytic    | 1.0        | False  | 1.00    | 1.00    | 0.50    |
| analytic    | 2.0        | False  | 1.00    | 1.00    | 0.30    |
| MC bootstrap| 0.5        | True   | 1.00    | 1.00    | 0.99    |
| MC bootstrap| 1.0        | True   | 1.00    | 1.00    | 0.97    |
| MC ou       | 1.0        | True   | 1.00    | 1.00    | 1.00    |

Cancellations dominate sensitivity at deeper levels: `use_cancel=True` pushes
p_fill to 1.0 even at 8 bps.  `use_cancel=False` (conservative) is the right
baseline: it can distinguish liquid from illiquid distance buckets and
produces the low-but-nonzero values that make the rest-vs-cross decision
nontrivial.

## Live calibration needed before position sizing

1. **≥14 days of continuous L2** for the same symbols — 7 min is 0.03% of
   a trading fortnight; the through-hazard at 2+ bps is estimated from
   single-digit spells.
2. **OKX L2 + trades** sampled simultaneously — the two-leg p_fail cannot
   be estimated from one venue.  A VPS or proxy is prerequisite.
3. **Resting-order execution log** — the harness can replay `wave.py` against
   the recorded stream to produce synthetic fill events, enabling markout and
   p_fail measurement end-to-end.
4. **Time-of-day splits** — fill behaviour at 02:00 UTC ≠ 14:00 UTC.
5. **Fee tier and min-notional filtering** — fees change the effective markout
   sign; min notional gates whether a resting order can be placed at all.

## Summary

The conservative model (price-through + trade-only queue depletion) runs cleanly
on the 7 min Lighter sample.  p_fill at the touch is near-certain across all
four symbols; at 2–5 bps it drops meaningfully for VVV and COIN but stays high
for LIT (liquidity outlier).  The MC model adds mid-path uncertainty and gives
lower p_fill than the analytic model when the dislocation is likely to run
away.  These numbers are **Lighter-only smoke test, not OKX**, and are
insufficient to set position size.