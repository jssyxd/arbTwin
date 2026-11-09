# Reply — two-leg balance execution design

Deliverables (all under `research/execution/`):

- **`design.md`** — sliced state machine (per-leg states, HALTED overlay, transition
  table); combined-basis top-up math `P̄_S ≥ P̄_L(1+β)` with incremental limit prices
  `p_max^buy = [Q'·P̄_S/(1+β) − C_L]/Δ` (mirror for sells), 4 worked cases incl. the
  marginal-tick trap; 3-zone top-up policy (cross / rest at limit / no-top-up inequality);
  order-type matrix per venue×state; slice sizing, lot/dust rounding; rebalance ratchet
  with A-S inventory skew; safety (markout toxic-stop, kill switch, outage HALT);
  validation plan with honest queue simulation tiers.
- **`diff-proposal.md`** — function-level changes for wave.py / engine.py / wavetrader.py /
  exitqueue.py / reconcile.py / risk.yaml / tests. Includes two prerequisite bug fixes:
  `on_fill` last-px overwrite (wave.py:426-428) and unnormalised `round_pnl_bps`.
- **`sources.jsonl`** — 16 citations (A-S, Perold, Almgren-Chriss, queue/fill-probability
  papers, VPIN, OKX/Lighter docs, Hummingbot XEMM).

No code edited. Own derivations marked [INFERENCE]; open verification items listed in
design.md §8.
