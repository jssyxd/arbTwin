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
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

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
    # 24/7 tokens that look like tickers and were once misclassified as equities
    "GRAM", "VVV", "LIT", "PUMP", "SPX", "ASTER", "ZEC", "XMR", "DASH", "MELANIA",
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
    basis_p50_bps: float = 0.0
    events: int = 0
    completion: float = 0.0
    trades_per_day: float = 0.0
    basis_p95_rth_bps: float = 0.0
    basis_p50_rth_bps: float = 0.0
    n_rth: int = 0
    capture_bps: float = 0.0
    net_per_round_bps: float = 0.0
    max_fee_bps_for_breakeven: float = 0.0
    basis_p95_bps: float = 0.0
    fill_probability: float = 0.0
    score: float = 0.0
    session: str = "crypto"
    maker_fee_bps: float = 0.0
    b_star_bps: float = 0.0
    spread_okx_bps: float = 0.0
    spread_lighter_bps: float = 0.0
    net_taker_bps: float = 0.0
    net_maker_bps: float = 0.0
    # net of the *armed* trigger: p95 of the session the engine may quote in, minus
    # the all-in breakeven b*.  Distinct from net_per_round_bps, which is built from
    # the measured event capture and can be positive while this is negative.
    net_rth_bps: float = 0.0
    preferred_mode: str = "hedge_now"
    reasons: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)

    def to_symbol_entry(self) -> dict:
        return {
            "symbol": self.symbol,
            "unit_ratio": round(self.unit_ratio, 8),
            "sigma_1h_bps": round(self.sigma_1h_bps, 2),
            "basis_sigma_bps": round(self.basis_sigma_bps, 2),
            "b_star_bps": round(self.b_star_bps, 2),
            "band_bps": round(self.band_bps, 2),
            "session": self.session,
            "preferred_mode": self.preferred_mode,
            # wave-mode contract: without these the engine has no thresholds
            "entry_bps_p95": round(self.basis_p95_rth_bps or self.basis_p95_bps, 2),
            "exit_bps_p50": round(self.basis_p50_rth_bps or self.basis_p50_bps, 2),
            "band_override_bps": round(self.basis_p95_rth_bps or self.basis_p95_bps, 2),
            "entry_deadline_s": 5400,
            "exit_deadline_s": 3600,
            "max_hold_h": 4.0,
            "spread_okx_bps": round(self.spread_okx_bps, 2),
            "spread_lighter_bps": round(self.spread_lighter_bps, 2),
            "capture_bps": round(self.capture_bps, 2),
            "net_per_round_bps": round(self.net_per_round_bps, 2),
            "net_rth_bps": round(self.net_rth_bps, 2),
            "max_fee_bps_for_breakeven": round(self.max_fee_bps_for_breakeven, 2),
            "events": self.events,
            "completion": round(self.completion, 3),
            "trades_per_day": round(self.trades_per_day, 2),
            "notes": {
                "ratio_cv_pct": round(self.ratio_cv_pct, 4),
                "return_corr": round(self.return_corr, 5),
                "score": round(self.score, 2),
                "basis_p95_bps": round(self.basis_p95_bps, 2),
                "basis_p95_rth_bps": round(self.basis_p95_rth_bps, 2),
                "basis_p50_rth_bps": round(self.basis_p50_rth_bps, 2),
                "capture_bps": round(self.capture_bps, 2),
                "events": self.events,
                "completion": round(self.completion, 3),
                "trades_per_day": round(self.trades_per_day, 2),
                "net_per_round_bps": round(self.net_per_round_bps, 2),
                "net_rth_bps": round(self.net_rth_bps, 2),
                "max_fee_bps_for_breakeven": round(self.max_fee_bps_for_breakeven, 2),
                "fill_probability": round(self.fill_probability, 3),
            },
        }

    def to_dict(self) -> dict:
        d = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}
        return d


def _log_line(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class RthGate:
    """Verdict of the arm-time RTH net check (`rth_net_gate`)."""

    symbol: str
    allow: bool
    net_rth_bps: Optional[float]      # None = unknown; never coerced to 0.0
    min_net_rth_bps: float
    reason: str

    @property
    def line(self) -> str:
        return f"{'PASS' if self.allow else 'BLOCK'} {self.symbol}: {self.reason}"


def net_rth_bps_of(entry: dict) -> Optional[float]:
    """RTH net edge of one `symbols.*.yaml` entry, or None when not derivable.

    None is not zero: an entry carrying neither the screened metric nor the two
    numbers it is built from is *unknown*, and unknown fails closed.
    """
    raw = entry.get("net_rth_bps")
    if raw is None:
        raw = (entry.get("notes") or {}).get("net_rth_bps")
    if raw is None:
        # entry_bps_p95 is what the engine arms (p95 of the tradable session, else
        # the 24h p95); b_star_bps is the all-in breakeven.
        p95 = entry.get("entry_bps_p95") or entry.get("band_override_bps")
        b_star = entry.get("b_star_bps")
        if p95 is None or b_star is None:
            return None
        raw = float(p95) - float(b_star)
    return float(raw)


def rth_net_gate(entry: dict, risk: Optional[dict] = None,
                 log: Optional[Callable[[str], None]] = None) -> RthGate:
    """Arm-time veto on the net edge of the session the engine may quote in.

    `screen_symbols` applies the same rule when it builds the universe; this is the
    second gate because a universe is a *file*: it can be stale, hand-edited, or
    written before the threshold existed — which is how R1 armed CRCL at a RTH net
    of 6.26 − 8.12 = −1.86 bps/round while the headline metric said +1.25.
    `screening.allow_negative_rth: true` arms such a symbol anyway, deliberately
    and visibly (the line still states how far under the floor it sits).
    """
    screening = (risk or {}).get("screening") or {}
    floor = float(screening.get("min_net_rth_bps", 0.0))
    allow_negative = bool(screening.get("allow_negative_rth", False))
    symbol = str(entry.get("symbol") or "?")
    net = net_rth_bps_of(entry)
    if net is None:
        allow = False
        reason = ("RTH net unknown (entry has no net_rth_bps and no "
                  "entry_bps_p95/b_star_bps pair) -> fail closed")
    elif net >= floor:
        allow, reason = True, f"RTH net {net:+.2f}bps >= min {floor:.2f}bps"
    elif allow_negative:
        allow = True
        reason = (f"RTH net {net:+.2f}bps < min {floor:.2f}bps -> armed anyway "
                  f"(screening.allow_negative_rth: true)")
    else:
        allow = False
        reason = (f"RTH net {net:+.2f}bps < min {floor:.2f}bps (p95 of the tradable "
                  f"session − b*); set screening.allow_negative_rth: true to override")
    gate = RthGate(symbol=symbol, allow=allow, net_rth_bps=net,
                   min_net_rth_bps=floor, reason=reason)
    (log or _log_line)(gate.line)
    return gate


def gate_universe(cfg: AppConfig,
                  log: Optional[Callable[[str], None]] = None) -> dict[str, RthGate]:
    """Run `rth_net_gate` over every approved symbol the live engine would arm."""
    return {str(e.get("symbol") or "?"): rth_net_gate(e, cfg.risk, log) for e in cfg.symbols}


def calibration_reject_reasons(calib, cv_max: float, corr_min: float) -> list[str]:
    """One inequality per line, each with its own bound and verdict.

    `calibrate()` fuses both bounds into `ratio cv 0.554% / return corr 0.9807
    outside [0.500%, 0.99]`, which reads like an interval and hides that the two
    checks are independent — a decisive double failure looked "borderline" in the
    R1 write-up.  Spell out which bound failed and by how much.
    """
    out: list[str] = []
    if calib.cv > cv_max:
        out.append(f"ratio cv {calib.cv * 100:.3f}% > max {cv_max * 100:.3f}% "
                   f"-> not one instrument")
    if calib.ret_corr < corr_min:
        out.append(f"return corr {calib.ret_corr:.4f} < min {corr_min:.4f} "
                   f"-> not one instrument")
    return out or list(calib.reasons or ["calibration failed"])


def event_study(basis_bps: "list[tuple[float, float]]", session_kind: str = "crypto",
                max_hold_bars: int = 4) -> dict:
    """Enter when |basis| >= p95, leave at |basis| <= p50 (or mark to market).

    `basis_bps` is [(timestamp_s, signed_basis_bps)] in chronological order, already
    restricted to the session the engine can trade.  Returns the conditional
    expectation that actually matters: mean capture per *event*, completion rate,
    and how many events per day.

    Why not `p95 − p50`: entries happen when the basis is at or beyond the tail, so
    the expected entry level is E[b | b>=p95] (~p99), not p95.  Using p95 understates
    capture; using it as if you always enter exactly at p95 also understates the
    fill level.  This measures the conditional mean and includes timeouts.
    """
    import statistics as _st
    from .anchor import Session as _S

    if len(basis_bps) < 50:
        return {"events": 0, "completion": 0.0, "capture_bps": 0.0, "per_day": 0.0,
                "note": "sample too small"}
    sess = _S(kind=session_kind)
    tradable = [(t, b) for t, b in basis_bps if sess.is_open(t)]
    pool = tradable if len(tradable) >= 60 else basis_bps
    if session_kind != "crypto" and len(tradable) >= 60:
        pass
    absb = [abs(b) for _t, b in pool]
    p95 = percentile(absb, 0.95)
    p50 = percentile(absb, 0.50)
    caps: list[float] = []
    completions = 0
    for i, (_t, b) in enumerate(pool):
        if abs(b) < p95:
            continue
        sign = 1.0 if b > 0 else -1.0
        exit_j = None
        for j in range(i + 1, min(i + 1 + max_hold_bars, len(pool))):
            if sign * pool[j][1] <= p50:
                exit_j = j
                break
        j_end = exit_j if exit_j is not None else min(i + max_hold_bars, len(pool) - 1)
        caps.append(sign * (b - pool[j_end][1]))
        if exit_j is not None:
            completions += 1
    if not caps:
        return {"events": 0, "completion": 0.0, "capture_bps": 0.0, "per_day": 0.0,
                "p95": p95, "p50": p50, "note": "no events"}
    span_days = max((pool[-1][0] - pool[0][0]) / 86400.0, 1e-9)
    return {"events": len(caps), "completion": completions / len(caps),
            "capture_bps": _st.fmean(caps), "per_day": len(caps) / span_days,
            "p95": p95, "p50": p50,
            "worst_bps": min(caps), "best_bps": max(caps)}


def classify_session(symbol: str, extra_crypto: Optional[set[str]] = None) -> str:
    """crypto = 24/7 (anchor never freezes) vs us_equity = 09:30-16:00 ET.

    Getting this wrong is expensive in one direction only: a 24/7 token labelled
    as an equity gets its anchor *frozen* outside US hours, which silently turns
    the engine into reduce-only for two thirds of the day.
    """
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
    # "one instrument" bounds, reported as two separate inequalities on rejection
    cv_max = float((cfg.risk.get("screening") or {}).get("max_ratio_cv_pct", 0.5)) / 100.0
    corr_min = float((cfg.risk.get("screening") or {}).get("min_return_corr", 0.99))
    # net edge of the session the engine may actually quote in (see rth_net_gate)
    min_net_rth = float((cfg.risk.get("screening") or {}).get("min_net_rth_bps", 0.0))
    allow_negative_rth = bool((cfg.risk.get("screening") or {}).get("allow_negative_rth", False))

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
        calib = calibrate(symbol, okx_series, lt_series, cv_max=cv_max, corr_min=corr_min)
        row.unit_ratio = calib.ratio
        row.ratio_cv_pct = calib.cv * 100
        row.return_corr = calib.ret_corr
        if not calib.tradable:
            row.reject_reasons.extend(calibration_reject_reasons(calib, cv_max, corr_min))
            continue

        # 3. volatility + band
        okx_closes = [okx_series[k] for k in sorted(okx_series)]
        sigma = sigma_1h_bps(returns_from_closes(okx_closes))
        row.sigma_1h_bps = sigma
        fee_book = fee_from_market_specs(spec_okx, spec_lt)
        fee_bps = fee_book.round_trip_bps(LIGHTER, OKX)
        row.fee_bps = fee_bps
        # close_type="post_only": a maker/maker round trip exits passively too.
        # Omitting it charges a taker exit (7 bps instead of 4) and rejects every
        # symbol for a cost the strategy never pays.
        row.maker_fee_bps = fee_book.round_trip_bps(LIGHTER, OKX, "post_only", "post_only",
                                                    close_type="post_only")
        latency = latency_cost_bps(sigma, mech.get(LIGHTER).taker_speed_bump_ms + 120)
        row.latency_bps = latency
        row.session = classify_session(symbol, extra_crypto)

        # basis distribution (historical, normalised).  The band is built from
        # the volatility of the *basis*, not of the price: a basis trade's risk
        # is the spread between the two venues.
        basis_series = [(lt_series[k] / calib.ratio - okx_series[k]) / okx_series[k] * 1e4
                        for k in sorted(set(okx_series) & set(lt_series))]
        basis = basis_series
        # The engine may only quote inside the reference session, so the trigger must
        # be the p95 of the *tradable* window — not of a 24/7 mix whose tail is
        # concentrated in closed hours (the transcript measured 78x there for SPY).
        from .anchor import Session as _Session
        _sess = _Session(kind=row.session)
        keys_all = sorted(set(okx_series) & set(lt_series))
        rth = [abs((lt_series[k] / calib.ratio - okx_series[k]) / okx_series[k] * 1e4)
               for k in keys_all if _sess.is_open(k / 1000.0)]
        row.n_rth = len(rth)
        if rth:
            row.basis_p95_rth_bps = percentile(rth, 0.95)
            row.basis_p50_rth_bps = percentile(rth, 0.50)
        row.basis_sigma_bps = statistics.pstdev(basis_series) if len(basis_series) > 2 else sigma
        band = entry_band_bps(row.basis_sigma_bps, fee_bps, cfg.band)
        row.band_bps = band
        row.basis_median_bps = statistics.median(basis) if basis else 0.0
        row.basis_p50_bps = percentile([abs(b) for b in basis], 0.50) if basis else 0.0
        row.basis_p95_bps = percentile([abs(b) for b in basis], 0.95) if basis else 0.0
        row.fill_probability = sum(1 for b in basis if abs(b) >= band) / len(basis) if basis else 0.0

        # 4. liquidity + manipulation cost
        try:
            okx_book = okx.book(symbol)
            lt_book = lighter.book(symbol)
        except Exception as exc:
            row.reject_reasons.append(f"book fetch failed: {exc}")
            continue
        # Break-even basis: fees + the half-spread you give up on BOTH legs twice
        # (entry and exit) + an adverse-selection margin.  Screening on fees alone
        # (4 bps) approves books like GRAM whose OKX spread alone is 9 bps.
        adverse_margin_bps = float((cfg.risk.get("screening") or {}).get("adverse_margin_bps", 1.0))
        row.spread_okx_bps = okx_book.spread_bps
        row.spread_lighter_bps = lt_book.spread_bps
        # b* must use the fee the strategy actually pays (maker, 4 bps), not the
        # taker-hedge round trip (10 bps) -- otherwise every symbol looks dead.
        row.b_star_bps = (row.maker_fee_bps
                          + 2.0 * ((row.spread_okx_bps + row.spread_lighter_bps) / 2.0)
                          + adverse_margin_bps)
        # The armed trigger is the RTH p95 (to_symbol_entry -> entry_bps_p95), so the
        # net that decides approval must be measured against that same number and
        # not against the 24h p95, whose tail can sit in closed hours.
        row.net_rth_bps = (row.basis_p95_rth_bps or row.basis_p95_bps) - row.b_star_bps
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
        # A round captures (p95 − p50), NOT p95: you enter at the tail and leave at
        # the median.  Booking p95 as gross overstates every projection by ~2x.
        entry_p95 = (row.basis_p95_rth_bps
                     if (row.session != "crypto" and row.n_rth >= 120) else row.basis_p95_bps)
        exit_p50 = (row.basis_p50_rth_bps
                    if (row.session != "crypto" and row.n_rth >= 120) else row.basis_p50_bps)
        study = event_study([(k / 1000.0, (lt_series[k] / calib.ratio - okx_series[k]) / okx_series[k] * 1e4)
                             for k in sorted(set(okx_series) & set(lt_series))],
                            session_kind=row.session,
                            max_hold_bars=int((cfg.risk.get("screening") or {}).get("max_hold_bars", 4)))
        row.events = int(study.get("events", 0))
        row.completion = float(study.get("completion", 0.0))
        row.trades_per_day = float(study.get("per_day", 0.0))
        row.capture_bps = float(study.get("capture_bps", 0.0)) if row.events else max(0.0, entry_p95 - exit_p50)
        p_fail = float((cfg.risk.get("screening") or {}).get("p_fail_prior", 0.15))
        abort_cost = p_fail * row.b_star_bps
        row.net_per_round_bps = row.capture_bps - row.b_star_bps - abort_cost
        # what maker fee would make this break even (the only lever the transcript names)
        spread_cost = row.spread_okx_bps + row.spread_lighter_bps
        row.max_fee_bps_for_breakeven = max(
            0.0, row.capture_bps - spread_cost - adverse_margin_bps
            - p_fail * (row.b_star_bps))
        row.net_taker_bps = entry_p95 - fee_bps - latency - cfg.limits.max_slippage_bps
        row.net_maker_bps = row.net_per_round_bps
        best_net = max(row.net_taker_bps, row.net_maker_bps)
        row.preferred_mode = "hedge_now" if row.net_taker_bps >= row.net_maker_bps else "dual_maker"
        row.score = best_net * max(row.fill_probability, 1e-6) * min(card.size_factor, 1.0)
        min_net = float((cfg.risk.get("screening") or {}).get("min_net_per_round_bps", 1.0))
        if row.basis_p95_bps < min_move_bps:
            row.reject_reasons.append(f"basis p95 {row.basis_p95_bps:.1f}bps below {min_move_bps}bps floor")
            continue
        if best_net <= 0:
            row.reject_reasons.append(
                f"net {row.net_per_round_bps:+.1f}bps/round < 0: capture {row.capture_bps:.1f} "
                f"(p95 {row.basis_p95_rth_bps or row.basis_p95_bps:.1f} -> p50 "
                f"{row.basis_p50_rth_bps or row.basis_p50_bps:.1f}, {row.events} events, "
                f"{row.completion:.0%} complete) - b* {row.b_star_bps:.1f} "
                f"(fees {row.maker_fee_bps:.1f} + spreads {row.spread_okx_bps:.1f}/{row.spread_lighter_bps:.1f} + margin)")
        elif best_net < min_net:
            row.reject_reasons.append(
                f"net {row.net_per_round_bps:+.1f}bps/round < min {min_net:.1f} "
                f"(thin: {row.trades_per_day:.2f} trades/day, {row.completion:.0%} complete)")
            continue
        # Armed-session economics: a symbol whose RTH p95 sits below b* loses money
        # on every round it completes, however good the 24h headline looks (R1 F7:
        # CRCL +1.25 headline vs −1.86 in the session the engine actually quotes).
        if not allow_negative_rth and row.net_rth_bps < min_net_rth:
            row.reject_reasons.append(
                f"RTH net {row.net_rth_bps:+.2f}bps/round < min {min_net_rth:.2f}: "
                f"p95rth {row.basis_p95_rth_bps or row.basis_p95_bps:.2f} − b* "
                f"{row.b_star_bps:.2f} (headline net_per_round "
                f"{row.net_per_round_bps:+.2f}bps is not the armed session)")
            continue
        if row.score < min_score:
            row.reject_reasons.append(f"score {row.score:.2f} < {min_score}")
            continue

        row.approved = True
        row.reasons.append(
            f"capture {row.capture_bps:.1f}bps (p95rth {row.basis_p95_rth_bps:.1f} − "
            f"p50rth {row.basis_p50_rth_bps:.1f}) − b* {row.b_star_bps:.1f} − "
            f"p_fail {p_fail:.2f}·b* = {row.net_per_round_bps:+.1f}bps/round; "
            f"RTH net {row.net_rth_bps:+.2f}bps/round (armed trigger − b*); "
            f"breakeven needs maker fee ≤ {row.max_fee_bps_for_breakeven:.1f}bps; "
            f"ratio {calib.ratio:.6f} cv {row.ratio_cv_pct:.3f}% corr {row.return_corr:.4f}; "
            f"sigma {sigma:.1f}bps band {band:.1f}bps; basis p95 {row.basis_p95_bps:.1f}bps; "
            f"net taker-hedge {row.net_taker_bps:.1f}bps / maker-maker {row.net_maker_bps:.1f}bps; "
            f"hits band {row.fill_probability:.1%} of hours; mode {row.preferred_mode}; score {row.score:.2f}"
        )

    # Rank by the net of the session the engine may quote in.  `score` is built from
    # the 24h basis tail, which for an equity can live entirely in closed hours; it
    # stays as the tie-break, not as the ranking key.
    approved = sorted([r for r in rows if r.approved],
                      key=lambda r: (-r.net_rth_bps, -r.score))[:max_symbols]
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
    # same ranking the top-N cut uses, so the file reads best-first
    approved = sorted([r for r in rows if r.approved], key=lambda r: (-r.net_rth_bps, -r.score))
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