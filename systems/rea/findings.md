# REA feasibility study — reverse-studying closed trading systems (CBB, TierZero, NeuralArb, XEMM, funding-arb-bot)

> Date: 2026-11-10. Repo: `W:/dynamicTWIN` (arbTwin, OKX↔Lighter two-leg basis).
> "REA" is ambiguous; both readings are covered explicitly:
> (A) **Reverse Engineering & Analysis** of software artifacts, and
> (B) **Rapid Evidence Assessment** — a structured, time-boxed evidence-synthesis method.
> Not legal advice. US + EU focus. Claims marked `[INFERENCE]` go beyond cited sources.
> Inputs read: `research/SYNTHESIS.zh.md`, `research/x-medium/findings.md`,
> `research/github/findings.md`, `.field-guide/*`.

---

## 0. Executive answer

1. **For the five named targets, binary RE is not worth it — and mostly not even possible.**
   CBB, TierZero, and NeuralArb are desk-side (server) trading operations: **no client binary
   carrying their logic exists on your side of the wire**. Their published writeups *are* the
   behavior spec, including parameters (CBB: `maxDelta` 800, `cancelDelta` 0.02,
   `takerRatioBuy/Sell` 0.05/0.10; TierZero break-evens; NeuralArb cost model — all already
   harvested in `research/x-medium/findings.md`). XEMM, funding-arb-bot, counterfactual5,
   and pfba are **open-source** (MIT-class licenses): read the code; nothing to reverse.
2. **The lawful, high-yield channel — public-surface analysis — is already exhausted for this
   project.** The repo's `research/` tree is a completed Rapid Evidence Assessment by
   accident: evidence table with claims+numbers+links, `[INFERENCE]` discipline, failed-search
   log, dated entries. Formalize it (§4), don't repeat it.
3. **Recommended budget: 0 hours of binary RE.** The only defensible client-app analysis case
   (§3.3) — interoperability with venues you hold accounts on — is moot here because OKX and
   Lighter publish REST/WS APIs and `arbtwin/venues/` already implements the hard parts
   (Lighter zk-signatures, nonce streaming; OKX WS/REST split).
4. Procurement beats reverse engineering: open components cover ~80% (execution, adapters,
   risk, reconciliation); only the basis-specific 20% (paired-basis pricing per
   `SYNTHESIS.zh.md` §2, wave integration, calibration) is genuinely yours to build (§5).

---

## 1. Legal boundaries (scope/legality first)

### 1.1 Regimes that govern (cited)

| Regime | What it says | Bearing on this project |
|---|---|---|
| **EULA / venue ToS (contract law)** | Anti–reverse-engineering clauses are common in venue client EULAs; violating them is breach of contract, not automatically a crime — but venue ToS also let the exchange **terminate your account**, which kills arbTwin's actual business. | Highest *practical* risk. Any analysis of an exchange client must start from its ToS. |
| **CFAA (18 U.S.C. § 1030)** — `Van Buren v. United States`, 593 U.S. 374 (2021); `hiQ Labs v. LinkedIn`, 9th Cir. 2022 | *Van Buren*: "exceeds authorized access" = entering parts of a system off-limits to you, not violating use restrictions. *hiQ* (on remand): scraping **publicly available** website data, even after a cease-and-desist, falls outside CFAA. | Reading public docs/repos/pages: outside CFAA. Touching **auth-protected endpoints, other accounts, or off-limits parts** of a system: squarely inside it. |
| **DMCA § 1201 (17 U.S.C. § 1201)** | Bans circumvention of effective technological protection measures and trafficking in circumvention tools. § 1201(f) provides a narrow **interoperability** exception for RE; 37 CFR § 201.40 lists triennial rulemaking exemptions (incl. limited security-research classes, conditionally). | Running Ghidra/Frida on a client that has anti-tamper/obfuscation = potential § 1201 exposure **independent of copyright infringement**. Tools like Frida on your *own* traffic without breaking protection are different from defeating obfuscation. [INFERENCE] |
| **EU Software Directive 2009/24/EC** | Art. 5: a lawful user may **observe, study, test** the program's functioning while performing acts they are otherwise entitled to. Art. 6: **decompilation** allowed only if (i) indispensable to achieve interoperability of an independently created program, (ii) the needed information is not otherwise readily available, (iii) confined to the necessary parts, and (iv) not used to build a substantially similar program. | The cleanest legal hook for client protocol study in the EU — but note Art. 6(ii): if the venue **publishes API docs**, decompilation's precondition fails. For OKX/Lighter: documented APIs ⇒ no decompilation basis. |
| **EU Trade Secrets Directive 2016/943** | Recital 16 / Art. 3(1)(b): know-how acquired by **reverse engineering a lawfully acquired product** is a lawful acquisition; the Directive targets theft and breach of confidentiality duties, not RE per se. | Lawful RE does not itself misappropriate trade secrets. But studying **leaked/stolen code** (e.g., a competitor's leaked repo) is unlawful acquisition — liability attaches downstream. |
| **Trade-secret law (US: DTSA/UTSA analog)** | Same shape: lawful acquisition (RE of a lawfully obtained copy) vs. improper means. | Same conclusion as above. |

### 1.2 What is allowed (green zone)

- Reading **public documentation, public API/SDK docs, published papers, patents, blog posts,
  X articles** (CBB, TierZero, NeuralArb writeups — already done).
- Reading **open-source repos** under their licenses (XEMM, funding-arb-bot,
  counterfactual5, pfba, nautilus_trader). MIT/Apache permit reuse with attribution;
  LGPL-3.0 (nautilus) permits linking with obligations.
- Observing behavior of **systems you interact with as an authorized user**: exchange reject
  codes, rate-limit responses, order-state transitions you trigger with your own orders.
  [INFERENCE] This is Art. 5 (2009/24/EC) territory: you're entitled to use the API, and
  observing responses during entitled use is the documented observation right.
- **Network traffic you generate yourself** against your own accounts, on documented or
  undocumented-but-authorized endpoints (Wireshark/mitmproxy on your own sessions).
- **Bug-bounty scopes** where the venue explicitly authorizes testing — in-scope only.

### 1.3 What is not allowed / high risk (red zone)

- **Bypassing authentication, rate limits, or other access controls** → CFAA ("parts of the
  system off-limits to you" after *Van Buren*) + DMCA § 1201 if a technical measure is
  circumvented + venue ToS breach with account termination.
- **Scraping private/auth-protected endpoints**, or continuing after cease-and-desist on a
  *non-public* endpoint — *hiQ* protects public-data scraping only.
- **Decompiling an exchange client with anti-circumvention** (obfuscated/packed binaries)
  → DMCA § 1201 exposure independent of what you learn; § 1201(f) is narrow and litigated.
- **Using stolen or leaked credentials/code**: studying a leaked proprietary repo is trade-
  secret misappropriation risk under both EU 2016/943 and US trade-secret law, even if you
  only "read" it.
- **Attacking/penetrating venues or other desks** — unambiguous CFAA/computer-misuse
  territory; also directly counterproductive (arbTwin depends on both venues staying healthy
  and on its own accounts staying open).
- **Ambiguous → lawful alternative**: probing an undocumented endpoint of a venue you use.
  [INFERENCE] Treat as ambiguous; the lawful path is your own authorized traffic observation,
  official support/SDK docs, or the venue's bug-bounty channel.

### 1.4 Clean-room process (the defensible workflow)

Sanctioned shape from *Computer Associates v. Altai*, 982 F.2d 693 (2d Cir. 1992)
(abstraction–filtration–comparison; clean-room development praised by the court):

1. **Spec team** studies only lawful inputs (public docs, papers, open code with compatible
   licenses, observed behavior) and writes a **behavior specification** — state machines,
   thresholds, formulas, invariants — with zero code copied from any encumbered source.
2. **Wall**: spec contains no code, only observable behavior; any open-source code used is
   handled via its **license** (MIT/Apache copy+attribute), not via the clean-room channel.
3. **Implementation team** builds from the spec; keep the provenance log (this file +
   `sources.jsonl` is that log for this repo).

arbTwin is effectively already on this path: `research/x-medium/findings.md` distilled
practitioner behavior into rules R1–R11 (a spec), and `research/github/findings.md`
separates license-clean code reuse from pattern reuse.

---

## 2. Technical yield: what public-surface analysis can actually reveal (ranked)

For a **closed trading system**, ranked by (value to arbTwin) ÷ (cost + legal risk):

| Rank | Source class | Reveals | Value | Cost/risk | For these targets |
|---|---|---|---|---|---|
| 1 | **Open-source repos** (XEMM, funding-arb-bot, counterfactual5, pfba) | *Everything implementable*: order flow, fill detection, hedge queue, retry/rollback, reconciliation, venue adapters (incl. a live-tested **Lighter** adapter) | Full | ~zero (license attribution) | Already cataloged in `research/github/findings.md` |
| 2 | **Practitioner writeups** (CBB article, TierZero blogs, NeuralArb post) | Mechanisms **and published parameters**: entry shape, kill-switches, cost models, break-evens, postmortems ($1.1m stale-feed loss) | Very high — this *is* the spec | ~zero | Already harvested (`research/x-medium`) |
| 3 | **API/SDK docs + error taxonomy** (OKX, Lighter) | Order state machine, rate limits, reject-code semantics, idempotency keys, margin modes → adapter correctness and robustness rules | High (execution layer) | Low | `arbtwin/venues/` built on this; error-taxonomy gap noted in SYNTHESIS §1 (idempotent resend TODO) |
| 4 | **Exchange-side behavior you trigger** (rejects, rate-limit responses, partial-fill sequencing) | Robustness rules: retry semantics, deadline behavior, exactly-once vs at-least-once | High (live ops) | Low (own account, own traffic) | Partially done via `state/*.jsonl` + `reconcile.py` |
| 5 | **Papers/talks** (Daru SSRN 7461901, Maglaras–Moallemi–Wang, OneChronos) | Structural laws: adverse selection at touch, fill probabilities, two-leg sync rarity | High (strategy priors) | ~zero | Already in `.field-guide/microstructure.md` |
| 6 | **Patents** | Architecture and claimed parameter ranges | Low–medium | Medium (search cost) | [INFERENCE] Crypto desks patent little of this; HFT-style execution patents (Cboe, etc.) cover exchange-side matching, not desk alpha — poor ROI here |
| 7 | **Job postings / engineering blogs** | Stack choices (Rust/Python, colocation, infra) | Low for edge; mild context | ~zero | Not worth dedicated effort |
| 8 | **Client-app / protocol RE** (Ghidra/Frida/mitmproxy on venue clients) | Wire formats, order types, session handling — *only what the client itself encodes* | Low (docs already cover it) | High (EULA/§1201/ToS) | Moot: OKX/Lighter document their APIs |
| 9 | **Binary RE of desk software** | n/a | n/a | n/a | **No artifact exists** (desk-side servers) |

### 2.1 What cannot be recovered, and why binary RE is a poor substitute

- **Alpha logic / signal generation** (what CBB/TierZero/NeuralArb actually trade on): lives
  server-side in their desks; no lawfully obtainable artifact contains it. Their public
  writeups disclose the *mechanism* (maker-first + conditional taker hedge, funding-snipe
  timing) but the live signal is venue/regime-dependent and re-tuned continuously —
  [INFERENCE] any snapshot (even a stolen one) decays faster than the effort to obtain it.
- **Private parameters and internal risk thresholds** (live `maxDelta`, current taker ratios,
  internal limits): same location problem; published values are stale the day they print.
- **Operational discipline** (CBB's $1.1m lesson was a *stale-feed* failure, not a signal
  failure): not encoded in any binary; it's process, which writeups convey better.
- Even in the hypothetical where a desk's client binary existed: you'd recover protocol glue,
  not the server's risk checks. The cost (clean-room + legal review + §1201 exposure) buys
  less than one week of public-surface reading (§2 ranks 1–5 already done in this repo).

---

## 3. Toolchain

### 3.1 Reverse Engineering & Analysis (artifact tools)

| Tool | What it's for | Lawful use in *this* project |
|---|---|---|
| **Ghidra** (NSA, open source) | Disassembly/decompilation of binaries | None currently — no lawful closed target with value. Would only matter for a venue client lacking docs (none here). |
| **IDA Pro** (hex-rays) | Same, commercial-grade | Same as above. |
| **radare2 / rizin** | Scriptable disassembly/analysis | Same; also useful on *open* binaries for protocol confirmation — still unnecessary here. |
| **Frida** | Dynamic instrumentation of running processes | Own-app testing (arbtwin's own processes) is unproblematic; venue-client instrumentation is EULA-encumbered and §1201-adjacent if protections exist. Not recommended. |
| **mitmproxy** | HTTPS interception | **Defensible**: intercepting *your own* client/API traffic you're authorized to generate (TLS trust on your own machine, your own account). Good for protocol-learning of authorized endpoints. |
| **Wireshark** | Packet capture | Same posture as mitmproxy; own traffic only. |
| **jadx / apktool** | Android DEX→Java, APK resources | Only if a venue's *mobile* app held undocumented API behavior you needed and ToS permitted — hypothetical; not needed (APIs documented). |
| **strings / binwalk** | Quick artifact triage, firmware extraction | No relevant artifact. |
| **Burp Suite** | Web/API security testing | In-scope bug-bounty testing only. |

**Conclusion (A):** every row is either unnecessary or contract-encumbered for the five named
targets; none unlocks anything the public surface hasn't.

### 3.2 Rapid Evidence Assessment (methodology tools)

The repo should formalize what it already practices:

- **PRISMA 2020** (Page et al., BMJ 2021;372:n71; 27-item checklist): use the *lite* subset —
  protocol (question + date bounds), search log (queries per source), inclusion criteria,
  evidence table, failed-search log, dated synthesis. `research/x-medium/findings.md` already
  implements items: scope statement, sources-with-numbers table, failed searches (§5), dated
  entries. Missing: an explicit inclusion/exclusion criteria list and a per-run search-query
  log. Add both to future sweeps.
- **Inclusion criteria** (recommended, matching current implicit practice): practitioner
  sources 2025+; concrete numbers required; primary source (post/repo/paper) over
  aggregation; venue pair relevant (CEX↔DEX perp or DEX↔DEX); exclude marketing pages and
  paywalled secondary digests.
- **Quality appraisal** (REA-style): numbers verifiable against source? postmortem vs
  aspiration? sample size/volume stated? conflicts (selling a product?) disclosed?
- **GRADE-like confidence** (GRADE: high/moderate/low/very low certainty; downgrade for
  imprecision, indirectness, inconsistency): map each finding to a confidence tier.
  Current practice is binary ([verified] / `[INFERENCE]`); a three-tier tag
  (`[HIGH: primary+numbers]`, `[MOD: secondary/indirect]`, `[INFERENCE]`) would match
  existing style at near-zero cost.

### 3.3 The few defensible client-app analysis cases

1. **Interoperability with a venue you hold an account on, using your own authorized
   traffic** (EU 2009/24/EC Art. 5 observation; DMCA § 1201(f) only if protection measures
   were absent or un-circumvented — [INFERENCE]; ToS permitting). Defensible, and *moot for
   arbTwin*: OKX/Lighter publish APIs and `arbtwin/venues/lighter.py` already implements zk
   signatures + nonce streaming.
2. **Venue bug-bounty scopes**: testing only what the venue authorizes, in scope, per its
   rules.
3. **Court/expert-ordered RE**: not on the horizon here.

Everything else (competitor clients, leaked code, venue probing) falls in §1.3.

---

## 4. Decision matrix — the specific targets

| Target | Closed artifact? | What exists publicly | Open license? | Reverse-study worth it? | Verdict / path |
|---|---|---|---|---|---|
| **CBB** (HIP-3↔IBKR equity perps) | **No binary** — desk-side bot | X article (mechanism + full parameter set + $1.1m postmortem), mirrored in `research/x-medium` | n/a | **No — nothing left to reverse** | Public-surface: **complete**. R3/R5/R7/R8 rules already mapped to arbTwin files. |
| **TierZero** (HL↔CEX funding) | **No binary** | Two blog posts: playbook + cost model | n/a | **No** | Public-surface: complete. Feeds R11 (funding-timestamp awareness). |
| **NeuralArb** (HL vs CEXs) | **No binary** | Cost-model post (fee stack, slippage curve, child splitting) | n/a | **No** | Public-surface: complete. Feeds R10 (clip caps, child orders). |
| **XEMM** (Pacifica↔HL) | — | **Open repo (Rust)** | Yes | **No RE — just read** | Reuse as *pattern* (4-layer fill dedup, hedge queue 256, profit-deviation cancel). License permitting, direct reuse with attribution; pattern-copy via clean-room otherwise. |
| **funding-arb-bot** (spot↔perp) | — | **Open repo** (risks.md, edges.md) | Yes | **No RE — just read** | Rules R4/R7 source. |
| **counterfactual5/funding-arb** | — | **Open repo (MIT)**, live-tested Lighter adapter | MIT | **No RE — read/reuse** | Strongest candidate for *component procurement* (§5): Lighter venue adapter + cross-venue rollback executor. |
| *(venue clients: OKX, Lighter)* | Closed apps | Public REST/WS docs | ToS | **Marginal; moot** | APIs documented; adapter done. Own-traffic observation (mitmproxy) is the lawful fallback if a gap ever appears. |

**Recommendation across the board: public-surface + clean-room over binary RE.** For the
three closed desks, "reverse study" as binary RE has **no target artifact** and would add
nothing beyond their own writeups. For the open systems, RE is semantically void — the code
is the ground truth. The only REA work that pays is (B): periodic evidence sweeps (§3.2).

---

## 5. Alternative: component procurement plan (open 80% / bespoke 20%)

| Layer (≈80%) | Open component | Provenance / status |
|---|---|---|
| Execution engine, OMS, backtest | **NautilusTrader v2** (LGPL-3.0): OKX + official Lighter adapters, `OmsType.HEDGING`, `OrderEmulator` | SYNTHESIS §8 path B; local 1.219.0 lacks Lighter — v2.0.0rc8 has it |
| Venue adapters (Lighter live-tested) | **counterfactual5/funding-arb** (MIT): Lighter/OKX adapters, cross-venue rollback, settle-mismatch planner | `research/github/findings.md` §2 |
| Two-leg state-machine patterns | **pfba** (MIT): `HedgeCycle`, `choose_lead_leg`, DEGRADED rebalance, patient-exit asymmetry, 3-way reconciliation | Same, §3.1–3.5 |
| Fill detection / hedge queue | **XEMM** (open): 4-layer dedup, bounded queue (256) with halt-on-overflow | `research/x-medium` §3 |
| Risk rules (published) | CBB `maxDelta`, funding-arb-bot kill-switch hierarchy, TierZero margin keeper | Rules R4–R8 already mapped in `research/x-medium` §4 |
| Data freshness gates | arbTwin `mechanics.py` (own, already) + CBB $1.1m lesson as motivation | `.field-guide/microstructure.md` |

**Bespoke 20% (basis-specific, must be arbTwin's own):**

1. **Paired-basis top-up pricing** off the filled leg's average entry (SYNTHESIS §2) —
   explicitly a gap in every open repo (github findings §6, gap #1).
2. **Wave integration**: `hedge_now` default entry, `taker_entry_ratio_bps`, fill-price
   notional-weighted averaging, abort-cost accounting.
3. **Calibration / venue unit semantics** (`unit_ratio`, funding units) — repo invariants,
   not portable.
4. **Lighter 300 ms speed-bump-aware latency budget** (measure, don't RE).

Licensing note: MIT components may be copied with attribution (license text retained);
LGPL (nautilus) used as a linked library keeps obligations manageable. If organizational
policy prefers zero mixing: use the open repos as **spec sources only** through the §1.4
clean-room wall — the behavior-level rules (R1–R11) already written in
`research/x-medium/findings.md` are exactly that spec.

---

## 6. Bottom line

- **Can "REA tools" reverse-study the cited closed systems?** For REA-as-Reverse-Engineering:
  no lawful, high-value target exists — the closed systems are server-side desks whose
  mechanisms are self-published, and the open ones need no RE. For REA-as-Rapid-Evidence-
  Assessment: yes, and the repo has effectively already done one; formalize with PRISMA-lite
  logging and GRADE-style confidence tiers for future sweeps.
- **Where the remaining edge actually is**: not in anyone's binary — in the paired-basis
  pricing math (SYNTHESIS §2), execution robustness, and venue-specific calibration, all of
  which are build-from-spec work with zero RE exposure.
