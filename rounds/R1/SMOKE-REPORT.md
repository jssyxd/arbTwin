# R1 live smoke + blocker fixes (2026-11-10)

User authorized a live smoke despite the HOLD verdict. This is an execution test, not an
edge claim.

## Result

- Lighter sell 0.2 CRCL filled (short). OKX post-only buy 0.2 at bid did not fill and was
  cancelled. Lighter leg was flattened immediately; final positions `{}`.
- Earlier failed attempts left OKX long 0.2 (post-only filled before Lighter errored);
  flattened at 68.06 with reduce-only IOC. Final OKX equity 31.464 (from 31.479),
  Lighter 27.614 (from 27.629): total cost ≈ $0.03.

## Bugs found and fixed

1. `LighterVenue.__init__` never created `_order_seq` → every live Lighter order raised
   `AttributeError`. Fixed with a monotonic `itertools.count` seed.
2. `lighter.SignerClient` was constructed outside a running event loop → aiohttp
   `RuntimeError: no running event loop`. Fixed by constructing it inside `AsyncBridge`.
3. Lighter blocks the China egress with `20558 restricted jurisdiction`. Added a
   Kuala-Lumpur tinyproxy tunnel (`127.0.0.1:3129`) and set `HTTPS_PROXY` for Lighter.
4. At $12 notional, CRCL rounds to 0.1 on both venues → Lighter notional $6.8 < $10
   `min_quote`, rejected `21706`. Raised the R1 cap to $15 and floor to $14; smoke used
   $14 and 0.2 units.

## Remaining review blockers (unchanged)

- CRCL RTH net is negative (6.26 − 8.12 = −1.86 bps); the live engine is armed but should
  not be treated as positive EV.
- Data cadence ~20 s, not 1.5 s.
- Kill switches (2 losses / 5% DD / 25 bps adverse) still missing from config.
- `registry: {}` for CRCL.
