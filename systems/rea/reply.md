# Reply — REA feasibility (reverse-study of CBB/TierZero/NeuralArb/XEMM/funding-arb-bot)

**Short answer: no — and for the best reason: there's nothing to reverse.**

- **REA-as-Reverse-Engineering**: CBB/TierZero/NeuralArb are server-side desks — no client
  binary exists on your side of the wire; their writeups *are* the behavior spec (parameters
  included, already harvested in `research/x-medium/findings.md`). XEMM,
  funding-arb-bot, counterfactual5, pfba are open-source — read them, no RE.
  Legal boundary map (CFAA/`Van Buren`+`hiQ`, DMCA §1201(f), EU 2009/24/EC Art.5–6,
  EU 2016/943, ToS; clean-room per *CA v. Altai*) and the few defensible client-app cases
  (own authorized traffic; bug-bounty scope) are in the findings.
- **REA-as-Rapid-Evidence-Assessment**: the repo has effectively already done one;
  formalize with PRISMA-lite logging + GRADE-style confidence tiers.
- **Recommendation**: 0 h binary RE; public-surface + clean-room only; procure the open 80%
  (nautilus v2, counterfactual5 Lighter adapter, pfba patterns) and build only the
  basis-specific 20% (paired-basis top-up pricing, wave integration, calibration).

Deliverables:
- `systems/rea/findings.md` — legal boundaries, yield ranking, toolchain, target matrix, recommendation
- `systems/rea/sources.jsonl` — 17 cited sources
- `systems/rea/reply.md` — this file
