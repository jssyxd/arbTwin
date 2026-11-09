# Round 1 — data, universe, live arming (2026-11-10)

## Goal
Prove the RWA/HIP-3 universe and the live plumbing before any strategy round:
OKX ↔ Lighter, CRCL only if it is the only pair that clears cost, $10–12/leg.

## Evidence

### 1. Screening (14 d, 19 requested RWA symbols)
`screen_rwa.log`:

- **approved 1/19: CRCL** score 0.04, unit ratio 0.9987, CV 0.045%, corr 0.9986,
  basis p95 8.96 bps, b* 8.12, net +1.25 bps/round, 0.28 trades/day, fill probability 0.035.
- KORU rejected: ratio CV 0.507% vs 0.500% (borderline), corr 0.9823 vs 0.99.
- QQQ rejected: net −0.7 bps/round (b* 6.2, capture 6.4).
- CBRS rejected: OKX moves 25 bps for $35k (< $40k depth gate).
- BMNR/US100/IBM/US500 rejected on unit calibration.
- ANTHROPIC/KIOXIA excluded before screening (unit 10x / stale quote).
- 30 d re-screen (8 symbols, `screen_rwa30.log`): **still only CRCL**; KORU CV 0.507%.

### 2. Data check
`round_check.json`: PASS. CRCL 14 rows, max gap 20.8 s, basis p50 4.39 / p95 6.80,
OKX spread p50 1.49 bps, Lighter 1.05 bps, RTH only, actions WAIT 13 / MAKER 1.
Caveat: the engine's snapshot cadence is ~20 s, not the claimed 1.5 s; `max_gap_s=30`
is a deliberate looser bound for R1 and must be tightened in R2.

### 3. Live arming
`rwa_live.log`, `alerts.jsonl`:
- service `arbtwin-rwa-live` active, `dry_run:false` in the latest snapshot;
- STARTUP position check: flat (both venues);
- equity: OKX 31.479 USD, Lighter 27.629 USDC;
- no trade yet: CRCL basis 0.4–5.1 bps vs entry 6.26; current `passive net` is negative
  at these levels, so the engine is correctly waiting.

### 4. Diff for R1
- `config/symbols.rwa.yaml` (CRCL, deadlines 900/900, hold 2 h)
- `config/risk.rwa.yaml` (one symbol, $12 cap, $10 floor, $15 naked, 3% stop)
- `scripts/round_check.py`
- `docs/ROUNDS-RWA-2026-11.md`
- live unit on VPS (not in repo): `arbtwin-rwa-live.service`, `rwa-check.timer`, `rwa-screen.timer`.

## Open blockers
1. SMB `//192.168.1.6/iflow` credentials missing; data currently on 25 G local disk.
2. CRCL is marginal: 0.28 trades/day and 3.5% fill probability; a real round may not
   trigger for many hours. R1 cannot claim a completed live round until it does.
3. `min_round_notional_usd` was 25 in the first live start; config now 10, but the
   process must be restarted after the config change and verified by a new sizing log.

## Reviewer asks
See `review-glm.md` / `review-qwen.md` for the independent verdicts.