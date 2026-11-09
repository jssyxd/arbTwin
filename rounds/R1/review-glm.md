# R1 independent review — glm-5.3 (2026-11-10)

Scope: only the 8 files named in the brief (README, screen_rwa.log, symbols.rwa.yaml,
round_check.json, rwa_live.log, alerts.jsonl, config/risk.rwa.yaml, docs/ROUNDS-RWA-2026-11.md §0–4).
No repo code opened, no sub-agents. `[INFERENCE]` marks judgment beyond the artifacts.

## 1. Universe — KEEP CRCL-only; KORU rejection stands

- **KEEP CRCL-only.** It is the only symbol clearing the gates on both the 14d screen
  (1/19, `screen_rwa.log`) and the 30d re-screen (README §1). Two independent windows
  agreeing is the strongest signal available.
- **KORU was NOT wrongly rejected.** The decisive gate is return correlation, not CV:
  `screen_rwa.log:25` / `symbols.rwa.yaml:164-168` record corr **0.9807** vs the 0.99
  "one instrument" floor — a clear fail, not borderline. CV 0.554% > 0.500% is an
  independent second fail. Even under README's softer figures (0.507% / 0.9823),
  correlation alone rejects.
- **FIX (docs only):** README's KORU citation (0.507% / 0.9823, "borderline") matches
  neither 14d artifact (0.554% / 0.9807) — it reads like 30d numbers pasted into the
  14d section. Same class of drift in §2: README says 14 rows / WAIT 13 / basis p95
  6.80, but `round_check.json` says n=15 / WAIT 14 / p95 6.67. Quote the checked-in
  artifacts verbatim.
- Carried into R2: CRCL's approval rests on **4 events, 0.28 trades/day, 3.5% fill
  probability, net +1.25 bps/round** — statistically thin; the p95 entry 6.26 bps is
  unstable at that sample size [INFERENCE]. KEEP for R1, re-verify as the sample grows.

## 2. Data check — PASS conditionally valid

- **Valid as an R1 plumbing check at the deliberately loosened bound**: 15 records /
  174 s, observed max gap 20.77 s < 30 s, `fails: []` (`round_check.json`). The ~12–20 s
  cadence is internally consistent (stale_s 9.25) and is workable for a 6.26 bps entry
  threshold with 900 s deadlines and 2 h holds [INFERENCE].
- **NOT valid for what R1's own bar demands** (docs §3: 数据缺口 <0.1% over 14 d):
  - The window is ~3 minutes / 14 intervals — it cannot estimate a 14-day gap rate.
  - The claimed 1.5 s cadence is unmet and unexplained; top-level `max_gap_s: 30.0`
    equals the threshold exactly, so as reported it is ambiguous whether that field is
    the observed max or the bound echoed back (symbol-level observed is 20.77) [INFERENCE].
  - Tension to resolve in R2: `risk.rwa.yaml` gates `max_book_age_s: 3.0` while
    snapshots tick at ~20 s — coherent only if book-age is enforced on live order-book
    fetches, not on `snapshot.jsonl` [INFERENCE].
- R2: tighten the bound as promised and run the check over ≥24 h of tape before trusting it.

## 3. Risk — safe for this account, two caveats

- Sizing vs equity (OKX 31.479 + Lighter 27.629 ≈ $59): worst reading $12/leg → $4
  margin/venue at 3x → 12.7% (OKX) / 14.5% (Lighter) margin usage, under the 35%
  per-venue cap. If `$12` is round-level across both legs (the sizing log compares $12
  against the *round* floor), per-leg exposure halves. Either way bounded.
  [INFERENCE: `max_notional_usd` does not state per-leg vs per-round; docs §4 says per leg.]
- $15 naked cap vs $12 leg: a full leg can go naked; with the 800 ms hedge budget and
  CRCL σ₁h 87.9 bps, an hour unhedged ≈ $0.13 expected adverse — survivable.
- 3% stop = 300 bps ≈ **66σ of basis** (σ 4.52 bps, p95 8.96 bps): it will essentially
  never trigger on basis; it is a backstop for outright leg failure. The effective
  basis exits are docs §4's 25 bps adverse rule and config's `max_mark_divergence_bps: 40`
  — docs (25) and config (40) disagree [FIX: align]. Risking up to 300 bps to earn
  1.25 bps/round is a poor standalone ratio; acceptable only because the pair is hedged
  and round notional is $12–24.
- One symbol / one round / both-flat-before-next (`wave`, `max_open_symbols: 1`): correct
  for R1. Residual risks: OKX minimum order size vs $12 notional unverified [INFERENCE];
  at 0.28 trades/day the practical hazard is config drift while idling — already
  demonstrated by the $25-floor incident below.

## 4. Live evidence — NOT sufficient; arming is asserted, not proven

- **No line in any provided artifact shows `dry_run:false`.** README §3 claims it is "in
  the latest snapshot", but that line appears in neither `rwa_live.log` nor
  `alerts.jsonl`. The only arming-adjacent line is `alerts.jsonl:6`:
  `startup position check: flat (no open positions)` — proves a position check ran,
  not that live order placement is enabled.
- The sizing lines actively **contradict** the arming claim:
  - `rwa_live.log:1` / `alerts.jsonl:1`: `sizing $12.00 below the round floor $25.00
    (equity okx=10000.00/lighter=10000.00)` — equity 10000/10000 is a placeholder; real
    equity is 31.479 / 27.629. The engine had no authenticated account view when these
    lines were written.
  - The `$25.00` floor is the pre-fix value (config is now $10; README blocker 3 admits
    the restart is unverified) — the captured live log predates the current config.
  - Docs §1/§7: OKX private API is BLOCKED on missing `OKX_API_PASSPHRASE`. With that
    missing, the OKX leg of a live round cannot be placed regardless of `dry_run`.
    [INFERENCE: the 31.479 equity figure can only have been read manually, not by the engine.]
- "Flat + no trade + correctly waiting (basis 0.4–5.1 bps vs entry 6.26)" is exactly
  what a stale or unarmed process also produces: consistent with, not proof of, arming.
  The waiting logic itself is right — basis below b* 8.12 net of fees → passive net
  negative → WAIT in 14/15 snapshots.

## 5. Single most important missing check

**Post-restart live-arming proof**: one log line from the restarted `arbtwin-rwa-live`
process showing `dry_run:false` together with real authenticated equity
(`okx≈31.5 / lighter≈27.6`) and the loaded `$10` floor. Real OKX equity in an engine log
is itself proof that `OKX_API_PASSPHRASE` is resolved. Nothing else in R1 compensates
for its absence.

## Secondary gaps (not changing the verdict)

- R1's own acceptance criteria (docs §3: 双边成交, no naked-leg timeout, net ≥ 0,
  gap <0.1%) — none demonstrated; README blocker 2 concedes a completed round is still
  owed before R1 is "done".
- README data-check citations don't match `round_check.json` (14 vs 15 rows, WAIT 13 vs
  14, p95 6.80 vs 6.67); KORU 14d citation doesn't match the log.
- SMB `//192.168.1.6/iflow` credentials: storage-only, does not gate trading.

VERDICT: HOLD

Universe, screening, risk caps, and the engine's WAIT discipline are sound, but live arming is claimed without a supporting line while the captured log shows placeholder equity (10000/10000), a stale $25 floor, and OKX private auth still blocked — restart the service, print the arming line with real equity, and let CRCL complete one round before proceeding to R2.
