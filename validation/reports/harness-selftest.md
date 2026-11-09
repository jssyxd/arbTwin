# validation/harness selftest report

**Timestamp**: 2026-11-09T16:18:51Z
**Result**: PASS (67.5s)

## PASS Fill models

- [PASS] **conservative: buy limit filled on trade below**: got 10.0
- [PASS] **conservative: buy limit NOT filled on trade above**: got 0.0
- [PASS] **conservative: sell limit filled on trade above**: got 10.0
- [PASS] **queue-reactive: same-level fill, no queue**: got 3.0
- [PASS] **queue-reactive: same-level fill, queue ahead**: got 1.5000
- [PASS] **conservative: book-update crossing sell limit**: got 10.0

## PASS Schemas & Manifest

- [PASS] **schema_quote**: 8 columns
- [PASS] **schema_l2_snapshot**: 6 columns
- [PASS] **schema_trade**: 8 columns
- [PASS] **schema_funding**: 9 columns
- [PASS] **schema_order**: 16 columns
- [PASS] **schema_fill**: 12 columns
- [PASS] **manifest_mode**: quote

## PASS Replay engine

- [PASS] **replay_run**: 4412 events, 14 signals

### Metrics

| Metric | Value |
|--------|-------|
| n_events | 4412 |
| n_book | 4000 |
| n_trade | 412 |
| elapsed_fake_s | 199.9 |
| n_signals | 14 |
| a_signals | 14 |
| b_signals | 10 |

## PASS Metrics

- [PASS] **p_fill_0bps_60s**: value=0.9955
- [PASS] **p_fill_5bps_60s**: value=0.9952
- [PASS] **time_to_pair**: value=0.309
- [PASS] **p_fail**: value=0.0
- [PASS] **markout**: value=-1.08
- [PASS] **capture_bps**: value=13.54
- [PASS] **abort_cost**: value=5.0

### Metrics

| Metric | Value |
|--------|-------|

## Commands

```bash
# Run selftest
python -m validation.harness.selftest
```
