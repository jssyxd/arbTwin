# GitHub Research Summary — Two-Leg Basis Execution Patterns

**Top finding**: No production open-source two-leg limit-order basis engine matches arbTwin's
`wave.py` design. The closest is **pranay123-stack/perp-funding-basis-arb** (paper-only) with a
13-state hedge cycle, partial-fill resizing, rebalance-via-top-up, patient/urgent exit routing,
and three-way reconciliation. **counterfactual5/funding-arb** has working Lighter+OKX adapters
and a pure-futures spread executor with rollback but uses market orders. NautilusTrader (31k★)
has the OMS/emulator infrastructure but no two-leg spread example.

Key reusable patterns: (1) hedge ratio via `base_exposure()` not raw quantity, (2) leg ordering
by depth (harder leg first), (3) re-peg resting orders every tick, (4) hedging leg always crosses
to bound unhedged window, (5) patient exit only rests lead leg, (6) rebalance adds before trimming.

Gap: no repo implements top-up pricing against the filled leg's average entry.

**Deliverables**: `research/github/findings.md` (full analysis), `research/github/sources.jsonl`
(16 sources with file:function references).