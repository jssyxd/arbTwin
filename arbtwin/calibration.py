"""P0-1  Unit-ratio calibration: are these two quotes the *same* instrument?

Cross-venue arbs die on exactly one mistake: comparing prices that do not share
a unit.  Live evidence from this market pair (2026-11-08, 7d of hourly closes):

    AAPL   ratio 0.9987  cv 0.033%  return-corr 0.9983   -> same instrument
    NVDA   ratio 0.9988  cv 0.031%  return-corr 0.9989   -> same instrument
    ETH    ratio 1.0001  cv 0.011%  return-corr 0.9997   -> same instrument
    ANTHROPIC ratio 10.38 cv 4.067% return-corr 0.7988   -> *different* instrument

The last one trades at ~10.4x on Lighter and the ratio wanders by 4%: it is a
premium/relative-value product, not a unit re-denomination.  Any "8% spread"
quote on it is mostly a contract-basis artifact.  So calibration is a hard
screening gate, not a nicety.

Verdicts
--------
same_instrument   ratio ~= 1 and stable          -> plain basis trading
ratio_stable      ratio != 1 but stable          -> re-denominated contract
                                                     (1000SHIB etc.), divide by ratio
reject_divergent  ratio unstable / returns decouple -> exclude from the universe
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class CalibrationResult:
    symbol: str
    ratio: float = 0.0
    cv: float = 1.0
    ret_corr: float = 0.0
    n: int = 0
    spread_bps_median: float = 0.0
    spread_bps_p95: float = 0.0
    verdict: str = "insufficient_data"
    reasons: list[str] = field(default_factory=list)

    @property
    def tradable(self) -> bool:
        return self.verdict in ("same_instrument", "ratio_stable")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ratio": round(self.ratio, 8),
            "ratio_cv_pct": round(self.cv * 100, 4),
            "return_corr": round(self.ret_corr, 5),
            "n": self.n,
            "spread_bps_median": round(self.spread_bps_median, 2),
            "spread_bps_p95": round(self.spread_bps_p95, 2),
            "verdict": self.verdict,
            "reasons": self.reasons,
        }


def _align(a: dict[int, float], b: dict[int, float]) -> tuple[list[float], list[float]]:
    keys = sorted(set(a) & set(b))
    return [a[k] for k in keys], [b[k] for k in keys]


def _returns(xs: Iterable[float]) -> list[float]:
    xs = list(xs)
    return [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs)) if xs[i - 1] > 0]


def _corr(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def calibrate(
    symbol: str,
    okx_closes: dict[int, float],
    lighter_closes: dict[int, float],
    cv_max: float = 0.005,
    corr_min: float = 0.99,
    same_unit_tolerance: float = 0.005,
    min_points: int = 48,
) -> CalibrationResult:
    """Decide whether the two price series are the same asset in different units."""
    res = CalibrationResult(symbol=symbol)
    ok, lk = _align(okx_closes, lighter_closes)
    res.n = len(ok)
    if res.n < min_points:
        res.reasons.append(f"insufficient overlapping hourly closes ({res.n} < {min_points})")
        return res

    ratios = [l / o for o, l in zip(ok, lk) if o > 0]
    if not ratios:
        res.reasons.append("non-positive prices in history")
        res.verdict = "reject_divergent"
        return res

    res.ratio = statistics.fmean(ratios)
    res.cv = statistics.pstdev(ratios) / res.ratio if res.ratio else 1.0
    res.ret_corr = _corr(_returns(ok), _returns(lk))

    basis_bps = [abs(r - res.ratio) / res.ratio * 1e4 for r in ratios]
    res.spread_bps_median = statistics.median(basis_bps)
    res.spread_bps_p95 = percentile(basis_bps, 0.95)

    if res.cv > cv_max or res.ret_corr < corr_min:
        res.verdict = "reject_divergent"
        res.reasons.append(
            f"ratio cv {res.cv * 100:.3f}% / return corr {res.ret_corr:.4f} "
            f"outside [{cv_max * 100:.3f}%, {corr_min}] -> not one instrument"
        )
        return res

    if abs(res.ratio - 1.0) <= same_unit_tolerance:
        res.verdict = "same_instrument"
        res.reasons.append(f"1:1 unit (ratio {res.ratio:.6f})")
    else:
        res.verdict = "ratio_stable"
        res.reasons.append(f"stable non-1 unit ratio {res.ratio:.6f} (denominated contract)")
    return res


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def band_from_series(xs: list[float], z: float = 2.0) -> tuple[float, float]:
    """(mean, sigma) of a basis series; helper for the replay and the monitor."""
    if not xs:
        return 0.0, 0.0
    return statistics.fmean(xs), (statistics.pstdev(xs) if len(xs) > 1 else 0.0)


def unit_ratio_for(config_entry: dict, calibration: Optional[CalibrationResult]) -> float:
    """Manual override wins; otherwise live calibration; otherwise refuse (0.0)."""
    override = float(config_entry.get("unit_ratio_override") or 0.0)
    if override > 0:
        return override
    if calibration and calibration.tradable:
        return calibration.ratio
    return 0.0