"""arbTwin — OKX <-> Lighter twin-venue basis engine.

Module map (P0-P8 from the research note):

    calibration.py   P0-1  unit ratio / same-instrument proof
    spread.py        P0-1  premium matrix + execution plan
    mechanics.py     P0-2  mark window, mark cap, OI/depth cascade, latency cost
    anchor.py        P1-3  tradfi / RFQ / median-of-indices anchor, session freeze
    band.py          P1-4  sigma x fee band, degenerate zero-vol branch
    fees.py          P1-5  fee by venue x order type x incentive epoch
    issuer_risk.py   P2-6  structural risk card
    manipulation.py  P2-7  cost to move the book, thin-reference penalty
    exitqueue.py     P2-8  passive exit tickets instead of market orders
"""

__version__ = "0.2.0"
__all__ = [
    "anchor", "band", "calibration", "config", "data", "engine", "exitqueue",
    "fees", "issuer_risk", "manipulation", "mechanics", "models", "monitor",
    "screening", "spread", "venues", "backtest",
]