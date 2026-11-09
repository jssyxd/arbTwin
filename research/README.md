# Research bundle — dual-venue two-leg basis execution (2026-11-09)

Captain: OMP (Herdr pane `wD:p1`). Workers: OMP CLI, one Herdr pane each, three model families.

| Dir | Job | Model | Focus |
|---|---|---|---|
| `SYNTHESIS.zh.md` | captain | — | integration: math, policy matrix, batching, rebalance, NT path |
| `x-medium/` | glm-x-medium | glm-5.3 | x.com / Medium / Reddit practitioner threads |
| `arxiv/` | qwen-arxiv → arxiv-fix | qwen3.8-max → deepseek-v4-pro | pairs/stat-arb, optimal execution, A–S, leg risk |
| `github/` | deepseek-github | deepseek-v4-pro | open-source two-leg engines, NT, Lighter adapters |
| `kaggle/` | glm-kaggle | glm-5.3 | datasets/notebooks, pitfalls, reusable methods |
| `execution/` | qwen-executor | qwen3.8-max | top-up math, three-zone policy, slicing, rebalance design |
| `nautilus/` | deepseek-nautilus | deepseek-v4-pro | NT v2 adapters, OMS, reconciliation, blueprint |

Each worker was told: read `.field-guide/*`, write only its own directory, no code edits,
no commit/push, no sub-agents, cite sources, mark inference.

Start with `SYNTHESIS.zh.md`; then `execution/design.md` (math) and
`nautilus/blueprint.md` (integration). Raw caches under `*/_cache/` and `*/tmp/` are
gitignored.