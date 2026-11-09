# Repo invariants (arbTwin)

## 2026-11-09 — quote() vs book() must not share a cache
`OkxVenue.quote()` reads WS `books5` (5 levels); `OkxVenue.book()` reads REST 400 levels.
README §1.5/§7.4: feeding the 5-level WS book into the depth gate understates AAPL 1%
depth by ~59x (12.4k vs 765k). Keep code paths and caches separate.

## 2026-11-09 — funding-rate units differ by venue
OKX `fundingRate` is a decimal per 8 h; Lighter `funding_rate` is a percentage per 1 h
(`0.0012` = 0.0012%/h ≈ 10.5% APR). README §5.3. Mixing them is a 100x-240x error.

## 2026-11-09 — wave.py has exactly one naked-leg state
`ENTRY_PARTIAL` is the only state holding unhedged risk. It is bounded by
`max_unhedged_notional_usd` (150 USD) and an adverse move stop
`max(adverse_move_stop_bps, adverse_sigma_mult * leg_sigma_1h_bps * sqrt(dt_h))`.
Abort path unwinds the filled leg passively; only risk stop and deadline escalation
use IOC/marketable orders.

## 2026-11-09 — exit prices are frozen on trigger
`WaveParams.freeze_px_on_trigger=True`: exit legs rest at the price captured at trigger
time, not at the live touch. Re-pegging behaviour differs between exit and abort paths;
check both when changing exit logic.