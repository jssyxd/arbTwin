# reply

Practitioner evidence (15 sources, 2025–2026) on twin-venue basis arb entry/exit/leg-risk.

1. **Nobody runs simultaneous dual post-only.** Every production writeup (CBB's $32B-volume HIP-3↔IBKR bot, TierZero, XEMM, FMZ) is maker-first on one venue, conditional taker hedge on the other.
2. **The proposed top-up pricing is validated twice**: FMZ prices leg 2 as `SUM_TARGET − leg1Price`; XEMM derives the maker quote from the hedge venue's executable price + fees + margin. Nobody prices off concurrent mids.
3. Numbers: taker/taker break-even ~4.5 bps/8h; maker RT 7 bps vs 13–20 bps taker-one-side; back-of-queue touch-quoting net-negative pre-fee on all three venues measured; only 3/8 tokens with ≥$10M OI covered 4 taker fills in 7d (Perpys).
4. Repeating kill-switches: retry-then-flatten in 60s (~0.15% cost), max-delta stop, stale-data veto, ±3 bps profit-deviation cancel.
5. 11 rules mapped to `wave.py`/`engine.py`/`exitqueue.py` (R1–R11); dead ends (@totofdn unretrievable) in §5.

Path: `W:\dynamicTWIN\research\x-medium\findings.md` · sources: `research/x-medium/sources.jsonl`
