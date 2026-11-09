# Reply — code/systems behind cited papers & case authors

**No paper author publishes official code.** Verified 2026-11-10: Leung & Li, Avellaneda (†2022),
Stoikov, Guéant, Almgren–Chriss, Cont–Kukanov, Bertram — none; CBB, TierZero, NeuralArb — closed-source
(cbb0fe GitHub = 0 repos; tierzero.dev/neuralarb.com link no code). The XEMM case author **djienne**
does publish: `CROSS_EXCHANGE_MARKET_MAKING_RUST` (174 commits/90d, Lighter zk signers, quote/inventory/
taker modules) — but unlicensed, reference-only.

Top 5 adoptable (details, file:function pointers, license flags in findings):
1. `joaquinbejar/market-maker-rs` — MIT, v0.7.0 yesterday, tested A-S/GLFT + risk gates
2. `AshJha0/electronic-trading` — MIT, Almgren-Chriss/placement/TCA platform, CI
3. djienne CEMMR/XEMM — live Lighter bot semantics (read-only)
4. `counterfactual5/funding-arb` — MIT, live Lighter adapter + rollback executor
5. `hudson-and-thames/arbitragelab` — BSD-3, Leung-Li OU/XOU thresholds (stale 2024)

Deliverables: `systems/papers/findings.md`, `systems/papers/sources.jsonl`.
