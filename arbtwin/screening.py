"""Screening: which OKX x Lighter pairs are actually two prices of one thing?

Order of the funnel (cheap filters first, network calls last):

1. **Existence**: listed and `active` on both venues.
2. **Unit calibration**: 7 days of hourly closes -> ratio, ratio CV, return
   correlation.  Anything that fails is not one instrument (see README on the
   ANTHROPIC counter-example) and is rejected with that exact reason.
3. **Volatility**: sigma of hourly returns -> the entry band the symbol can
   support, and whether the pair has enough movement to clear costs at all.
4. **Liquidity**: 1% depth on both books, floor and the manipulation-cost
   profile (a 10 bps move for $200 is not a market, it is a suggestion).
5. **Structural risk**: OI utilisation, cascade proxy, published OI limit,
   margin fractions, registry flags.
6. **Score**: expected net edge per round trip, times a probability proxy
   (how often the basis actually reaches the band, measured on the historical
   basis distribution) — divided by nothing.  Simplicity beats a ratio nobody
   can explain at 3 a.m.

The output is exactly what the engine eats: `unit_ratio`, `sigma_1h_bps`,
`session`, plus the reasons that produced them.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Optional

from .band import entry_band_bps, returns_from_closes, sigma_1h_bps
from .calibration import calibrate, percentile
from .config import AppConfig
from .data import both_hourly, candidate_symbols
from .fees import fee_from_market_specs
from .issuer_risk import build_card
from .manipulation import profile
from .mechanics import build_mechanics, cascade_proxy, evaluate_venue, latency_cost_bps
from .models import LIGHTER, OKX, MarketSpec
from .venues.lighter import LighterVenue
from .venues.okx import OkxVenue

CRYPTO_HINTS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON", "TRX", "SUI",
    "APT", "ARB", "OP", "MATIC", "POL", "DOT", "ATOM", "NEAR", "LTC", "BCH", "ETC", "FIL",
    "UNI", "AAVE", "MKR", "CRV", "COMP", "SNX", "LDO", "EIGEN", "ENA", "ETHFI", "ONDO",
    "WLD", "PEPE", "SHIB", "BONK", "WIF", "FLOKI", "DOGS", "NOT", "TIA", "SEI", "INJ",
    "JUP", "PYTH", "JTO", "RAY", "HYPE", "TRUMP", "FARTCOIN", "VIRTUAL", "AI", "TAO",
    "RENDER", "FET", "AR", "STX", "KAS", "HBAR", "XLM", "ALGO", "ICP", "FTM", "S", "MOVE",
}


@dataclass
class ScreenRow:
    symbol: str
    okx_inst: str
    lighter_market_id: str
    approved: bool = False
    unit_ratio: float = 0.0
    ratio_cv_pct: float = 0.0
    return_corr: float = 0.0
    sigma_1h_bps: float = 0.0
    basis_sigma_bps: float = 0.0
    band_bps: float = 0.0
    fee_bps: float = 0.0
    latency_bps: float = 0.0
    depth_okx_1pct: float = 0.0
    depth_lighter_1pct: float = 0.0
    depth_okx_density: float = 0.0
    depth_lighter_density: float = 0.0
    basis_median_bps: float = 0.0
    basis_p95_bps: float = 0.0
    fill_probability: float = 0.0
    score: float = 0.0
    session: str = "crypto"
    maker_fee_bps: float = 0.0
    net_taker_bps: float = 0.0
    net_maker_bps: float = 0.0
    preferred_mode: str = "hedge_now"
    reasons: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)

    def to_symbol_entry(self) -> dict:
        return {
            "symbol": self.symbol,
            "unit_ratio": round(self.unit_ratio, 8),
            "sigma_1h_bps": round(self.sigma_1h_bps, 2),
            "basis_sigma_bps": round(self.basis_sigma_bps, 2),
            "band_bps": round(self.band_bps, 2),
            "session": self.session,
            "preferred_mode": self.preferred_mode,
            "notes": {
                "ratio_cv_pct": round(self.ratio_cv_pct, 4),
                "return_corr": round(self.return_corr, 5),
                "score": round(self.score, 2),
                "basis_p95_bps": round(self.basis_p95_bps, 2),
                "fill_probability": round(self.fill_probability, 3),
            },
        }

    def to_dict(self) -> dict:
        d = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}
        return d


def classify_session(symbol: str, extra_crypto: Optional[set[str]] = None) -> str:
    names = CRYPTO_HINTS | (extra_crypto or set())
    for prefix in ("1000", "1M"):
        if symbol.startswith(prefix) and symbol[len(prefix):] in names:
            return "crypto"
    return "crypto" if symbol in names else "us_equity"


def screen_symbols(cfg: AppConfig, okx: OkxVenue, lighter: LighterVenue,
                   days: int = 7, min_score: float = 0.0,
                   max_symbols: int = 40, verbose: bool = False,
                   only: Optional[list[str]] = None, pause_s: float = 0.12) -> list[ScreenRow]:
    mech = build_mechanics(cfg.venues)
    registry = cfg.risk.get("registry") or {}
    extra_crypto = set((cfg.risk.get("crypto_symbols") or []))
    min_depth = float((cfg.risk.get("screening") or {}).get("min_depth_usd_1pct", 20_000))
    min_move_bps = float((cfg.risk.get("screening") or {}).get("min_median_basis_bps", 3.0))
    max_oi_util = float((cfg.risk.get("screening") or {}).get("max_oi_utilization", 0.35))

    rows: list[ScreenRow] = []
    wanted = {s.upper() for s in only} if only else None
    for symbol, okx_inst, lt_id in candidate_symbols(okx, lighter):
        if wanted and symbol.upper() not in wanted:
            continue
        rows.append(ScreenRow(symbol=symbol, okx_inst=okx_inst, lighter_market_id=lt_id))
    if verbose:
        print(f"candidates: {len(rows)}")

    for idx, row in enumerate(rows):
        if idx and pause_s:
            time.sleep(pause_s)     # courtesy throttle: OKX allows 20 req / 2 s per IP
        symbol = row.symbol
        spec_okx = okx.specs([symbol]).get(symbol) or okx.specs().get(symbol)
        spec_lt = lighter.specs([symbol]).get(symbol)
        if not spec_okx or not spec_lt:
            row.reject_reasons.append("not listed on both venues")
            continue

        # 2. calibration
        try:
            okx_series, lt_series = both_hourly(okx, lighter, symbol, days)
        except Exception as exc:
            row.reject_reasons.append(f"history fetch failed: {exc}")
            continue
        calib = calibrate(symbol, okx_series, lt_series)
        row.unit_ratio = calib.ratio
        row.ratio_cv_pct = calib.cv * 100
        row.return_corr = calib.ret_corr
        if not calib.tradable:
            row.reject_reasons.extend(calib.reasons or ["calibration failed"])
            continue

        # 3. volatility + band
        okx_closes = [okx_series[k] for k in sorted(okx_series)]
        sigma = sigma_1h_bps(returns_from_closes(okx_closes))
        row.sigma_1h_bps = sigma
        fee_book = fee_from_market_specs(spec_okx, spec_lt)
        fee_bps = fee_book.round_trip_bps(LIGHTER, OKX)
        row.fee_bps = fee_bps
        row.maker_fee_bps = fee_book.round_trip_bps(LIGHTER, OKX, "post_only", "post_only")
        latency = latency_cost_bps(sigma, mech.get(LIGHTER).taker_speed_bump_ms + 120)
        row.latency_bps = latency
        row.session = classify_session(symbol, extra_crypto)

        # basis distribution (historical, normalised).  The band is built from
        # the volatility of the *basis*, not of the price: a basis trade's risk
        # is the spread between the two venues.
        basis_series = [(lt_series[k] / calib.ratio - okx_series[k]) / okx_series[k] * 1e4
                        for k in sorted(set(okx_series) & set(lt_series))]
        basis = basis_series
        row.basis_sigma_bps = statistics.pstdev(basis_series) if len(basis_series) > 2 else sigma
        band = entry_band_bps(row.basis_sigma_bps, fee_bps, cfg.band)
        row.band_bps = band
        row.basis_median_bps = statistics.median(basis) if basis else 0.0
        row.basis_p95_bps = percentile([abs(b) for b in basis], 0.95) if basis else 0.0
        row.fill_probability = sum(1 for b in basis if abs(b) >= band) / len(basis) if basis else 0.0

        # 4. liquidity + manipulation cost
        try:
            okx_book = okx.book(symbol)
            lt_book = lighter.book(symbol)
        except Exception as exc:
            row.reject_reasons.append(f"book fetch failed: {exc}")
            continue
        row.depth_okx_1pct, cov_okx, comp_okx = okx_book.depth_covered(100.0)
        row.depth_lighter_1pct, cov_lt, comp_lt = lt_book.depth_covered(100.0)
        dens_okx, _, _ = okx_book.depth_density_usd_per_bps()
        dens_lt, _, _ = lt_book.depth_density_usd_per_bps()
        row.depth_okx_density = dens_okx
        row.depth_lighter_density = dens_lt
        man_okx = profile(okx_book, symbol, OKX)
        man_lt = profile(lt_book, symbol, LIGHTER)
        # compare notional-per-bp inside the covered part of the band: a level
        # cap truncates dense books (BTC: 400 levels ≈ 5 bps) long before 1%
        min_density = min_depth / 100.0
        if min(dens_okx, dens_lt) < min_density:
            row.reject_reasons.append(
                f"depth density OKX ${dens_okx:,.0f}/bp ({cov_okx:.0f}bps covered, "
                f"{'full' if comp_okx else 'truncated'}) / Lighter ${dens_lt:,.0f}/bp "
                f"({cov_lt:.0f}bps, {'full' if comp_lt else 'truncated'}) < ${min_density:,.0f}/bp")
            continue
        if man_okx.thin_reference and man_lt.thin_reference:
            row.reject_reasons.append("both books fetch a <$25k move inside 25bps")
            continue

        # 5. structural risk
        try:
            okx_stats = okx.stats(symbol)
            lt_stats = lighter.stats(symbol)
        except Exception as exc:
            row.reject_reasons.append(f"stats fetch failed: {exc}")
            continue
        v_okx = evaluate_venue(mech.get(OKX), okx_stats, okx_book)
        v_lt = evaluate_venue(mech.get(LIGHTER), lt_stats, lt_book)
        card = build_card(symbol, [spec_okx, spec_lt], [okx_stats, lt_stats], registry)
        for verdict in (v_okx, v_lt):
            if not verdict.allow:
                row.reject_reasons.extend(verdict.reasons)
        if not card.allow:
            row.reject_reasons.extend(card.hard_flags)
        if lt_stats.oi_utilization > max_oi_util:
            row.reject_reasons.append(f"Lighter OI utilisation {lt_stats.oi_utilization:.0%} > {max_oi_util:.0%}")
        if row.reject_reasons:
            continue

        # 6. score: expected net edge * fill probability, best of the two modes
        row.net_taker_bps = row.basis_p95_bps - fee_bps - latency - cfg.limits.max_slippage_bps
        row.net_maker_bps = row.basis_p95_bps - row.maker_fee_bps
        best_net = max(row.net_taker_bps, row.net_maker_bps)
        row.preferred_mode = "hedge_now" if row.net_taker_bps >= row.net_maker_bps else "dual_maker"
        row.score = best_net * max(row.fill_probability, 1e-6) * min(card.size_factor, 1.0)
        if row.basis_p95_bps < min_move_bps:
            row.reject_reasons.append(f"basis p95 {row.basis_p95_bps:.1f}bps below {min_move_bps}bps floor")
            continue
        if best_net <= 0:
            row.reject_reasons.append(
                f"cost exceeds the 95th-pct basis move (taker-hedge {row.net_taker_bps:.1f}bps, "
                f"maker/maker {row.net_maker_bps:.1f}bps)")
            continue
        if row.score < min_score:
            row.reject_reasons.append(f"score {row.score:.2f} < {min_score}")
            continue

        row.approved = True
        row.reasons.append(
            f"ratio {calib.ratio:.6f} cv {row.ratio_cv_pct:.3f}% corr {row.return_corr:.4f}; "
            f"sigma {sigma:.1f}bps band {band:.1f}bps; basis p95 {row.basis_p95_bps:.1f}bps; "
            f"net taker-hedge {row.net_taker_bps:.1f}bps / maker-maker {row.net_maker_bps:.1f}bps; "
            f"hits band {row.fill_probability:.1%} of hours; mode {row.preferred_mode}; score {row.score:.2f}"
        )

    approved = sorted([r for r in rows if r.approved], key=lambda r: -r.score)[:max_symbols]
    for r in rows:
        if r.approved and r not in approved:
            r.approved = False
            r.reject_reasons.append(f"ranked outside top {max_symbols}")
    return rows


def build_symbols_yaml(rows: list[ScreenRow], generated_at: Optional[float] = None) -> dict:
    """Render the universe file the engine consumes.

    `preferred_mode` is a *hint for human review* (which execution mode had the
    better historical economics), not a constraint: the engine re-decides every
    poll from live fees, basis and greeks.
    """
    approved = [r for r in rows if r.approved]
    rejected = [r for r in rows if not r.approved]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(generated_at or time.time())),
        "universe": {
            "candidates": len(rows),
            "approved": len(approved),
            "rejected": len(rejected),
        },
        "approved": [r.to_symbol_entry() for r in approved],
        "rejected": [
            {"symbol": r.symbol, "reasons": r.reject_reasons[:4],
             "ratio_cv_pct": round(r.ratio_cv_pct, 4), "return_corr": round(r.return_corr, 4),
             "net_taker_bps": round(r.net_taker_bps, 2)}
            for r in rejected
        ],
    }