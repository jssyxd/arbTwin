# Twin-venue long/short basis arb — practitioner evidence (x.com / Medium / forums)

Research date: 2026-11-09. Scope: entry policy (maker-first vs taker-both), second-leg sizing
after partial fill, exit batching, kill-switches, and which venue/asset regimes pay. Sources are
practitioner posts 2025–2026, weighted toward CEX↔DEX perp pairs with concrete numbers. All
claims below carry a source in §2. [INFERENCE] marks reasoning beyond a source.

Repo context read before writing: `README.md` §1–§3, `arbtwin/wave.py` (full `decide()`,
`WaveParams`), `arbtwin/engine.py` (`_execute_plan`, `_poll_maker`), `arbtwin/exitqueue.py`,
`arbtwin/wavetrader.py`.

---

## 1. Executive answer (with numbers)

**The user's core problem — legs not filling together — is the *normal* condition, not the
exception, in every writeup found.** No successful practitioner runs "two post-only orders at
the touch and wait". The convergent pattern across sources is:

1. **Maker-first on the venue that must be passive (thin/fee-advantaged), taker hedge on the
   liquid/fast venue, conditional on fill.** CBB ($32B volume, the largest dataset in this
   research): "Only if we get filled on Hyperliquid, we open the corresponding short on IBKR."
   TierZero: "Leg HL first (maker), immediately hit CEX taker after HL confirms."
   XEMM bot: maker price is *derived from the executable taker hedge price* (§3). arbTwin's
   `action=ENTER` path already implements this (Lighter post-only lead → OKX IOC); the `wave`
   dual-post-only entry is the variant practitioners treat as the harder, rarer case.

2. **The second leg after a partial first fill is priced off the filled leg's actual average
   entry, so the *pair* edge holds — nobody found prices top-ups off concurrent mids.** Two
   independent implementations do it exactly the way the user wants:
   - FMZ/luoyelittledream Polymarket bot: `leg2Price = SUM_TARGET - leg1Price` — the second
     leg's limit price is *reverse-computed from the pair target minus the first leg's actual
     price*, so the combined edge is locked at submission, and both orders go out concurrently.
   - XEMM Rust bot: `price = (HL_bid * (1 - taker_fee)) / (1 + maker_fee + profit_rate)` — the
     maker quote is computed from the *other* leg's executable hedge price plus fees plus a
     safety margin, not from the maker venue's own mid.
   - CBB accumulates the IBKR hedge delta (min 55 NVDA shares to beat the $1 fee minimum) —
     i.e., the hedge is batched by size threshold, and the trigger to hedge is the *accumulated
     filled delta vs the position*, not a mid-price signal.
   [INFERENCE] None of the sources states "price the top-up off the filled leg's average entry"
   in those words, but both concrete implementations price the second leg off the first leg's
   actual fill/entry such that the pair clears a target — which is exactly the user's rule.
   This is the strongest evidence-backed validation of the user's proposed change.

3. **Entry thresholds, with numbers:** TierZero: taker/taker break-even ≈ 4.5 bps/8h; <8 bps
   marginal unless one leg passive; **target 10 bps minimum**; require smoothed (8h rolling
   average) spread ≥ 3–4× round-trip fee. NeuralArb: maker round-trip ~0.07% (7 bps), forced
   taker one side 0.13–0.20% (13–20 bps); break-even 1.3 bps/8h funding spread at maker with
   7d hold. Perpys: at $10k/7d after 4 taker fills, only 3 of 8 tokens with ≥$10M OI on both
   legs covered fees — taker/taker is mostly dead at retail size; maker/maker is the only
   broadly viable retail shape on DEX↔DEX funding gaps.

4. **Exits are batched, deadlined, escalating — the same shape as `exitqueue.py`.** XEMM
   cancels stale maker quotes at 60s and cancels if profit deviates ±3 bps. CBB's system
   cancels/re-quotes when price moves `cancelDelta` (0.02 for NVDA). funding-arb-bot: maker
   first with 60s timeout → taker fallback. NeuralArb desks: unwind within 60 seconds of a
   funding sign-flip; exit HL first on unwind (lock the high-slippage leg first). TierZero:
   14-day hard time-in-trade cap. Ladder-into-deadline-then-IOC is structurally confirmed;
   no source contradicts arbTwin's 0/2/5 bps ladder + `escalation_bps` 25 design.

5. **Kill-switches that repeat across sources:** max unhedged delta (CBB `maxDelta` 800 shares
   — stop trading the market), retry-then-flatten within a time budget (funding-arb-bot: 3
   retries in 60s, then close the filled leg; residual cost ~0.15% of the gap accepted),
   data-freshness veto (funding-arb-bot: refuse to trade if last read >2 min old; CBB lost
   $1.1m to a stale IBKR feed), stale-quote cancel (XEMM 60s; CBB `cancelDelta`), symbol bench
   after consecutive hedge failures (arbTwin already has this), and a hard basis-drift stop
   (+1% per position in funding-arb-bot; CBB manually closed $120m of naked gold shorts).

**Bottom line:** the practitioner consensus validates arbTwin's risk architecture and
invalidates pure simultaneous dual-post-only entry. The two changes with the strongest
evidence: (a) make maker→conditional-taker-hedge the *default* entry path with the hedge priced
off the filled leg, and (b) replace "wait for the other leg at the touch" with XEMM-style
computation of the second leg's limit price from the first leg's actual fill so the pair
clears the threshold by construction.

---

## 2. Evidence table

| source | date | claim | numbers | link |
|---|---|---|---|---|
| CBB (@Cbb0fe) equity perps arb article (X, read via hyperindex mirror) | 2026-09-02 | Maker-first on HIP-3, hedge IBKR only after HL fill; delta accumulates to min hedge size; maxDelta kill-switch | min hedge 55 NVDA (IBKR $1 fee min), max 400/order, maxDelta 800, slippage cap 0.1, makerOffset 0.12, cancelDelta 0.02, takerRatio 0.05/0.1, preMarketOffset 0.04, limit 110000 NVDA; Nov $850m vol / $500k profit; Jan $1.7B vol / $600k funding; $1.1m loss on $120m naked gold; 3% silver dislocation → $600k day; $32B / 10 months | x.com/Cbb0fe/article/2095250548744405297 |
| r/algotrading Polymarket cross-venue arb (u/File-Environmental) | 2026-06-09 | Passive-only entries force one-leg-first; unhedged residual lost 38% of gross; stale-quote adverse selection is the dominant residual-loss driver | arb +$8,293, directional −$3,184 (38% of gross), 3,858 fills, ~$96k vol, win rate 50.2→48.3→43.4 monthly, LoL +$1,983/−$1,480 | reddit.com/r/algotrading/comments/1u17e2v |
| FMZ/quant001 (luoyelittledream) Polymarket dual-leg bot | 2026-03-04 | Leg 2 price reverse-computed from pair target minus leg 1 actual price; both submitted concurrently; unhedged leg managed with floor/TP/stop; cancel leg 2 before closing leg 1 | leg2Price = SUM_TARGET − leg1Price; example fill: leg1 0.34 + leg2 0.60 = 0.94 vs target 0.97; FLOOR_PRICE 0.05; ORDER_TIMEOUT_S loop with 1s poll | dev.to/quant001/...-2do6 |
| FMZ/luoyelittledream spot-futures arb lessons | 2025-12-16 | Single-leg risk → immediate market rollback + cooldown; market orders also rejected → limit-first + market fallback ×3; illiquid ticker lags 0.1–0.5% (1–3% volatile) so price off depth book; re-verify z-score before sending; P&L off actual AvgPrice | retry 3×; ticker-vs-book deviation 0.1–0.5% normal, 1–3% volatile; rollback cost accepted | fmz.com/digest-topic/10826 |
| TierZero HL↔CEX funding arb playbook | 2026-06-09 | HL maker leg first, CEX taker after confirm; 40%-adverse-move sizing; 8h rolling spread must clear 3–4× fees; 14d cap; pre-position float (bridge 15–40 min) | ~2.5× effective leverage; post-only RT ~0.08%; 34% APR example clears fees <1 day; margin keeper poll 15–30s, top up <150% maintenance | tierzero.dev/blog/hyperliquid-funding-rate-arbitrage-cex |
| TierZero older cost model | 2025-10-18 | Break-evens and persistent spreads by asset; HL maker rebate; residual fill delta = entry basis, log it | taker/taker BE ~4.5 bps/8h; <8 bps marginal unless passive; target ≥10 bps; BTC +4–8, ETH 15–20 spikes (24–48h MR), SOL 10–18 persistent; HL maker −0.2 bps rebate | tierzero.dev/blog/hyperliquid-funding-rate-arbitrage-binance-bybit |
| NeuralArb HL vs CEXs cost model | 2026-04-24 | Fee stack, slippage curve, child-order splitting, funding-snipe timing, 60s reversal unwind | maker RT ~0.07%, taker-one-side 0.13–0.20%; BE 1.3 bps/8h maker/7d; HL slip 5 bps@$100k, 12@$500k, 28@$1m; clip <0.5% of 24h vol; 3–5 children 30–60s apart (~40% slip cut); post-only 2–3 bps improvement ~60% of attempts; desks enter 10–15 min pre-funding | neuralarb.com/2026/04/24/... |
| XEMM Rust bot (Pacifica maker / HL taker) | 2026 repo | Maker price derived from taker hedge price; ±3 bps profit-deviation cancel; 60s stale cancel; 4-layer deduped fill detection; exactly one hedge per fill; maker halts on unqueueable hedge | profit_rate_bps 15 (safety margin); profit_cancel_threshold_bps 3; order_refresh 60s; hedge queue capacity 256; maker fee 1.5 bps / taker 4 bps (config) | github.com/djienne/XEMM_..._PACIFICA_HYPERLIQUID |
| Hunch net-edge model | 2026-08-19 | Second-leg move risk nonlinear; partial fill splits matched/unmatched, track separately; size off unwind loss not gross edge | 100-pair example: $5 gross → $2.20 after depth → $0.60 after fees → −$0.40 after 1¢ second-leg move; matched 70 + unmatched 30 framing | hunch.trade/journal/prediction-market-arbitrage-fees-slippage-fill-risk |
| alexanderlmoran/funding-arb-bot risks.md | 2026 repo | Partial-fill rule (retry 3×/60s then flatten, ~0.15% re-flatten cost); maker-first 60s→taker; +1% basis-drift kill; kill-switch hierarchy; data-freshness veto; funding-flip exit after 2 consecutive negative reads | retry 3 in 60s; re-flatten ~0.15% of gap; funding read staleness >2 min = no trade; maker fill rate <50%/month = colocation or taker regime | raw.githubusercontent.com/alexanderlmoran/funding-arb-bot/main/risks.md |
| same repo edges.md | 2026 repo | Maker-only cuts round-trip 60–80% but adverse selection is the bottleneck; funding-snipe only viable maker-first; alt funding dispersion numbers | maker RT −60–80%; HL alts 0.05–0.15%/hr vs Bybit 0.01%/8h; HL allocation cap 30%; alt cap 15% of capital | raw.githubusercontent.com/alexanderlmoran/funding-arb-bot/main/edges.md |
| Daru Finance touch-quoting study (SSRN 7461901) | 2025–2026 | Captured half-spread does not cover 10s post-fill drift on Bybit/Binance/HL; deeper quoting loses less per fill but fills 12% as often | net 10s markout −0.84/−0.39/−0.46 bp; touch −0.72 vs 5th level −0.33 bp but 12% fill rate; queue position worth ~0.8 bp on HL | daru.finance/research/crypto-adverse-selection |
| Perpys scanner (live) | 2026-11-09 | DEX↔DEX funding gaps net of fees; only 3/8 tokens with ≥$10M OI both legs covered 4 taker fills in 7d; sub-$1M-OI markets hidden (a $10k position moves them) | 237 tokens; e.g. ETH Aster↔Lighter net −$4.57 on $10k/7d after 4 taker fills; BTC −$6.27; XMR +$42.57; hourly gaps "usually gone within two" hours | perpys.com/arbitrage/ |
| @thelearningpill thread (snippet only) | 2026 ~Aug | Index products (US500) both legs 20x preferred over single stocks because index moves less → lower leg risk | "both legs at 20x"; full text unretrievable (X blocked) | x.com/thelearningpill/status/2084274353039106241 |
| Boros fixed-rate article (snippets only) | 2026-01 | Hedge *each leg individually*, not the net trade, to kill funding-flip risk; size capped by 0.2% orderbook constraint | 0.2% orderbook constraint; partial text only | x.com/boros_fi/article/2001831621817962761 |

---

## 3. Mechanism / playbook comparison

| dimension | CBB (HIP-3↔IBKR) | TierZero (HL↔CEX) | XEMM (Pacifica↔HL) | FMZ Polymarket | funding-arb-bot (spot↔perp) | arbTwin wave (today) |
|---|---|---|---|---|---|---|
| entry shape | maker on DEX, taker hedge on IBKR after fill; also taker when ratio big enough | HL maker first, CEX taker immediately after confirm | maker quote derived from taker hedge price; hedge fires on fill event | both legs concurrent, leg2 price = target − leg1 actual | maker-first 60s → taker fallback | both legs post-only at touch; ENTER mode = Lighter lead + OKX IOC |
| second-leg pricing | accumulate delta, hedge ≥55 shares | immediate taker at market after lead confirms | from hedge venue's book + fees + profit margin | reverse-computed from pair target | retry missing leg 3×/60s | re-peg unfilled leg at the *touch* (live book) |
| unfilled-leg deadline | maxDelta 800 kill | 14d time cap; sub-0.5% spread filter | 60s stale cancel, ±3 bps profit cancel | FLOOR/TP/stop + last-minute cancel | 60s then flatten | entry_deadline 5400s, then passive unwind |
| exit batching | cancelDelta re-quotes; liquidity-driven dynamic close thresholds | spread < threshold or 14d; unwind HL-side first | profit-deviation cancel | hold to settlement; early TP | funding ≤0 ×2 reads | frozen price + ladder (0/2/5 bps) + 60min deadline + one IOC |
| naked-leg budget | maxDelta 800 shares (~hard stop) | 40% adverse move sizing; float reserve | hedge queue overflow → halt new maker | last-minute stop loss | close filled leg; ~0.15% cost | $150 notional / adverse stop / sigma stop |
| data staleness | IBKR feed freeze → $1.1m loss; added freshness checks | bridge latency 15–40 min float | 4-layer fill dedup | ORDER_TIMEOUT + retry | >2min stale = no trade | quote TTL (mechanics gate) |

Key structural contrasts for arbTwin:

- **CBB's parametrization is the closest published cousin of wave's per-symbol config**: a
  maker size, a maker offset vs fair, a cancel-delta, separate taker ratio thresholds for buy
  vs sell, a per-market position limit, and a session-specific extra offset (pre-market) —
  directly analogous to wave's `entry_bps` / `hysteresis` / `requote_after_s` / direction
  thresholds / `no_new_entry_min_before_close`. The one CBB mechanism wave lacks: **taker
  ratio thresholds — cross the spread on the leader when the dislocation is big enough**,
  with buy and sell thresholds set asymmetrically (0.05 vs 0.10).
- **XEMM's fill detection is a design warning**: arbTwin's dry-run uses a move-through
  heuristic; XEMM needed *four* deduplicated layers (WS fill feed, 500 ms REST order poll,
  position monitor, pre-cancel check) to guarantee exactly one hedge per fill. The wave
  live path relies on venue fill reports; the position-monitor layer (ground truth) is the
  piece worth copying if fill reports are ever delayed or dropped.
- **Daru's result reframes wave's `min_edge_bps=2.0`**: at the *back* of the queue, quoting
  the touch has negative expected value before fees on all three venues measured (−0.39 to
  −0.84 bp at 10 s). Wave quotes *at* the touch, so its entry fill is precisely the fill
  most likely to be adverse-selected. The mitigations used by practitioners: quote only when
  the basis is already dislocated (wave does), cancel fast on profit deviation (XEMM ±3 bps),
  or move to conditional-taker-hedge entry which pays the taker fee but eliminates queue
  adverse selection on the leader.

---

## 4. Concrete rules mapped to arbTwin files/functions

| # | rule (evidence) | arbTwin location | change |
|---|---|---|---|
| R1 | Price the second leg off the first leg's actual fill so the pair clears the entry threshold (FMZ `SUM_TARGET−leg1Price`; XEMM hedge-derived maker price) | `arbtwin/wave.py: _entry_orders()`, `decide()` ENTRY_PARTIAL re-peg loop (lines ~324-329), `Leg.filled_px` | In `ENTRY_PARTIAL`, re-quote the unfilled leg not at the touch but at `px_pair_target − filled_leg.filled_px` (converted by `ratio` and side), floored/capped at book touch ± `max_passive_bps`; recompute so combined basis ≥ `min_edge_bps` |
| R2 | Hedge conditionally on leader fill; derive leader quote from hedge venue's executable price + fees + margin (XEMM formula) | `arbtwin/engine.py: _execute_plan()` (ENTER path), `arbtwin/spread.py build_plan` | Make ENTER the default for `wave` too when passive_net < entry threshold but executable edge (leader maker + hedge taker) clears costs; maker price from `(okx_touch*(1∓taker_fee))/(1±maker_fee+margin_bps)` |
| R3 | Taker-ratio escape hatch: cross the spread on the leader when dislocation is large (CBB `takerRatioBuy/Sell`, asymmetric) | `wave.py: decide()` ENTRY_QUOTING | Add `taker_entry_ratio_bps` (per side) — if basis ≥ entry × ratio, send leader as IOC instead of post-only |
| R4 | Retry the missing leg within a time budget, then flatten the filled leg (funding-arb-bot 3×/60s; ~0.15% re-flatten cost accepted) | `wave.py: ENTRY_PARTIAL`, `_abort()` | Keep passive abort, but add bounded retry count of the missing leg *at pair-target pricing* before entering ABORT_UNWIND |
| R5 | Stale-quote cancel: kill the resting quote when profit deviates ±3 bps or age > 60 s (XEMM); CBB `cancelDelta` 0.02 | `wave.py: requote_after_s` (5 s), `WaveParams.hysteresis` | Tighten: cancel (not just re-peg) when live pair edge of remaining quote < 0; add `cancel_delta_bps` on pair-edge deviation, mirroring CBB |
| R6 | Size off the unwind loss, not the gross edge (Hunch); track matched vs unmatched separately | `engine.py: _size_plan()`, `wave.py: Round.filled_venues()` | Compute worst-case unwind (cross spread + 1–2 ticks + fees on the *filled* leg) at entry; cap `qty_okx` so that loss ≤ per-round risk budget |
| R7 | Data-freshness veto: no trade if last quote/anchor read > N s old (funding-arb-bot 2 min; CBB's $1.1m stale-feed loss) | `mechanics.py` quote TTL, `engine._gate_symbol()` | Extend gate: refuse ENTRY_QUOTING→fill transitions when either book snapshot is older than `quote_ttl*2`; alert on venue feed gap (CBB's failure mode) |
| R8 | Kill-switch hierarchy with wired actions, not alerts only (funding-arb-bot soft/hard/halt) | `engine.py: benched`, `hedge_fail_streak`, `emergency_stop_loss_pct` | Already present; add CBB-style `max_delta` (base-asset quantity mismatch between legs) as a stop-trading trigger distinct from notional |
| R9 | Exit the high-slippage leg first on unwind (NeuralArb); HL-side locked first | `wave.py: EXIT_QUOTING/ABORT_UNWIND`, `exitqueue.py plan()` | Sequence the two exit legs: quote/IOC the thinner venue first, then the liquid one — currently both exit legs are symmetric |
| R10 | Cap per-venue clip vs 24h volume and split children (NeuralArb: clip <0.5% of 24h vol; 3–5 children 30–60 s; ~40% slippage cut) | `engine.py: _target_notional()`, `spread.py` sizing | Add `min(clip, 0.005 * vol24h)` cap and optional child-order split for `qty_okx` above the cap |
| R11 | Enter after CEX funding settlement; exit/avoid before flips (TierZero; NeuralArb 10–15 min pre-window entries; 60 s reversal unwind) | `engine.py` timing, `wave.py max_hold_h` | Since OKX settles 8h and Lighter 1h: add funding-timestamp awareness to entry veto (don't open the OKX leg minutes before its settlement when carry is adverse) |

Rules R1/R2 are the two the user asked for specifically; both have two independent
implementations in the evidence.

---

## 5. Failed searches / open questions

- **@totofdn original thread (GLFTmm / HL↔Lighter)**: no direct hits on x.com or mirrors.
  README references it from an earlier research phase (`research/` prior work), but the
  thread itself is not retrievable now: x.com blocks automated access; search snippets only
  surfaced venue-comparison pages (perpfinder, exchange-compare) not the account. **Open.**
- **x.com article URLs blocked for all**: CBB and Boros articles unreadable via direct fetch;
  CBB recovered via blog.hyperindex.org full-text mirror; Boros only via search snippets
  (medium.com and substack both ECONNREFUSED from this network). Boros numbers unverified.
- **thelearningpill full thread**: only the first-post snippet (US500, 20x, index-vs-stock
  leg-risk argument). Substack unreachable; no thread-reader mirror found. **Open.**
- **rattibha PDF mirror of CBB**: ECONNREFUSED.
- **Direct fill-rate / time-to-fill statistics for post-only legs on Lighter/OKX**: not found
  anywhere. Closest proxies: Daru (12% fill rate for 5th-level vs touch on Bybit), XEMM's
  4-layer detection (implies fill events are lossy), Polymarket post (passive-only constraint
  acknowledged, no fill-probability numbers). **Open — needs own measurement via
  `state/monitor.jsonl`, as README §1.4 already suggests.**
- **Medium tag pages / algorithmic-trading tag browsing**: Medium itself unreachable
  (ECONNREFUSED on every medium.com URL); all Medium content was accessed via search
  snippets, dev.to mirrors, and fmz.com mirrors. gwrx2005 futures-spot article
  (margin-offset futures-futures construction) only partially recovered via snippet.
- **Paradex / dYdX specific execution writeups**: nothing with concrete leg-execution
  numbers surfaced; searches returned marketing pages. **Open.**

## 6. Surprises (for `.field-guide/`)

- The single most expensive practitioner failure found (CBB, −$1.1m) was not a market event
  but a **stale hedge-venue feed** causing the bot to "correct" a phantom delta into $120m of
  naked gold shorts — the exact failure mode arbTwin's `quote()` vs `book()` cache separation
  and `mechanics` TTL gates exist to prevent. Worth a dated field-guide note tying the CBB
  incident to `mechanics.py` freshness checks as motivation.
- Concurrent post-only both legs at the touch is *nobody's* production entry in this sample;
  even the Polymarket passive-only trader (forced into it by wide markets) lost 38% of gross
  arb to the resulting residuals. The dual-maker mode in arbTwin should be treated as the
  exception path, not the default, exactly as README §1.4's "only maker/maker has space"
  framing suggests.
- XEMM ships a *bounded hedge-event queue* (256) whose overflow **halts new maker placement**
  and persists intents to JSONL — "exposure is never silently dropped" is stated as a design
  invariant, mirroring arbTwin's intent/round JSONL audit trail philosophy.
- Perpys hides sub-$1M-OI markets with the argument "a $10k position moves them, and spread
  plus slippage, not included here, exceed the funding" — an external confirmation of
  arbTwin's depth-density gate over a naive depth@1% check.
- Daru: front-of-queue vs back-of-queue on HL BTC is worth ~0.8 bp per fill — larger than
  two of the three venue-level net markouts. [INFERENCE] For wave, which quotes at the touch
  with zero queue-priority modelling in dry-run, dry-run fills at the touch are *optimistic*
  relative to a real back-of-queue fill; the wavetrader move-through rule partially
  compensates but queue position is unmodelled.
