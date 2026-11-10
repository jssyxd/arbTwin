# R2 — fix the incident + ANT lessons (2026-11-11)

Source of truth: `rounds/R1/INCIDENT-2026-11-10.md`, `rounds/R1/review-{glm,qwen}.md`,
`W:/twinCheck/.herd-discuss/SYNTHESIS-ANT.md` (ant-cross-arb audit), live logs on
`192.168.1.98:~/arbTwin/state/`.

## Incident
OKX post-only filled 0.2 CRCL while Lighter failed (jurisdiction, then REST timeouts).
The round sat `ENTRY_PARTIAL`; book REST timeouts made the abort path skip the symbol.
Manual reduce-only IOC flattened at 65.77; total loss ≈ $0.321.

## What ANT teaches us
1. **UNKNOWN ≠ 0**: a failed position/book read must freeze entries and force a flatten,
   never be treated as flat. ANT's `gateway [] on error` read a live short as flat.
2. **Size the hedge from the actual fill, not the plan**: ANT sent `hl_filled*static_ratio`
   and over-hedged 10–43%; our leg 2 must use actual filled qty and current unit ratio.
3. **Close paths must be tested**: ANT's close/ROI/time-stop raised `NameError` and were
   swallowed; ours must have a test that proves a naked leg is flattened with no book.
4. **One ratio constant is wrong**: ANT's 12.7 vs exchange 13.166. Our `unit_ratio` must
   be re-checked/refreshed, not frozen forever.
5. **Journal + startup barrier**: ANT had 40 restarts with live positions and no journal.
6. **Kill switch must be reachable under degraded reads** (ANT's quota starved it).
7. **Priority lane**: closes must outrank quotes when the venue is slow/rate-limited.

## Fix ownership (disjoint files)
| Worker | Files | Deliverable |
|---|---|---|
| fix-exec | `arbtwin/wave.py`, `arbtwin/engine.py`, `tests/test_r2_exec.py` | data-outage flatten, cancel other leg on first fill, VWAP, rounds.jsonl, kill switch |
| fix-venues | `arbtwin/venues/{base,okx,lighter,lighter_trade}.py`, `config/venues.yaml`, `tests/test_r2_venues.py` | split proxies, timeout/retry, Lighter-first confirmation, order query fallback, unit-ratio refresh |
| fix-risk | `config/risk.rwa.yaml`, `arbtwin/screening.py`, `tests/test_r2_risk.py` | RTH net metric/warn, 25 bps stop, naked cap < notional, kill switches, registry |
| fix-observe | `scripts/round_check.py`, `arbtwin/monitor.py`, `tests/test_r2_observe.py` | cadence measurement, P&L/residual metrics, journal report |

## Acceptance
- `pytest tests/` green.
- A scripted simulated round with OKX fill + Lighter failure leaves **flat** without a book.
- Live service restarts, `dry_run=False`, real equity, split proxies; first order path uses
  Lighter ack before OKX.
- `rounds.jsonl` has one line per round; `abort_cost_bps` present.
- Kill switch file stops new entries and flattens within 5 s.