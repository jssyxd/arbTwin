"""The engine: universe -> matrix -> gates -> two-leg execution -> exits.

Execution model (and why it looks like this)
-------------------------------------------
OKX and Lighter are not symmetric venues:

* OKX is a centralised, low-latency venue whose IOC taker order is the fastest
  thing in the pair;
* Lighter's taker path carries a speed bump (300 ms on Standard accounts) and,
  today, charges no fee, while its post-only path has no bump at all.

So the trade is: **post passively on Lighter, hedge immediately with an IOC on
OKX once Lighter confirms**.  The unhedged window is bounded by
`max_unhedged_ms` and is the entire risk budget of the trade.  If the hedge
cannot be sent inside that budget, the leading leg is unwound instead of being
left naked.

Every gate that can veto is called from `_gate_symbol`, in the documented
order, and the first veto wins with a reason string.  Nothing in this file
computes a price: pricing lives in `spread`, calibration in `calibration`,
venue facts in `venues/*`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import Optional

from concurrent.futures import ThreadPoolExecutor

from .anchor import AnchorBook, Session
from .band import entry_band_bps, exit_band_bps, max_position_usd, returns_from_closes, sigma_1h_bps
from .calibration import calibrate, CalibrationResult
from .config import AppConfig, write_state
from .exitqueue import ExitQueue, ExitTicket
from .fees import FeeBook, build_fee_book, fee_from_market_specs
from .issuer_risk import build_card
from .manipulation import profile
from .mechanics import MechanicsBook, build_mechanics, evaluate_venue, latency_cost_bps
from .screening import gate_universe
from .models import (LIGHTER, OKX, Book, ExecutionPlan, Fill, GateVerdict, MarketSpec,
                     MarketStats, OrderRequest, SpreadRow, VenuePosition)
from .spread import build_plan, compute_row, summarise
from .reconcile import FillEvent, PositionLedger
from .wave import RoundState, WaveParams
from .wavetrader import WaveTrader
from .venues.lighter import LighterVenue
from .venues.okx import OkxVenue

log = logging.getLogger("arbtwin.engine")


@dataclass
class SymbolState:
    symbol: str
    unit_ratio: float
    sigma_1h_bps: float
    basis_sigma_bps: float
    band_override_bps: float
    spec_okx: MarketSpec
    spec_lighter: MarketSpec
    session: Session = field(default_factory=Session)
    calibration: Optional[CalibrationResult] = None
    entry_ts: float = 0.0
    entry_okx: float = 0.0
    entry_lighter: float = 0.0
    qty_okx: float = 0.0
    qty_lighter: float = 0.0
    side: str = "flat"
    cooldown_until: float = 0.0
    last_gate: Optional[GateVerdict] = None
    last_row: Optional[SpreadRow] = None
    wave_params: Optional[WaveParams] = None


@dataclass
class Broker:
    """Leg executor.  `dry_run` makes every order a deterministic simulation."""

    okx: OkxVenue
    lighter: LighterVenue
    dry_run: bool = True
    slippage_bps: float = 2.0

    def place(self, order: OrderRequest, spec: MarketSpec) -> Fill:
        venue = self.okx if order.venue == OKX else self.lighter
        if self.dry_run:
            px = order.px * (1 + (self.slippage_bps / 1e4 if order.side == "buy" else -self.slippage_bps / 1e4)) \
                if order.tif != "post_only" else order.px
            fee = spec.maker_fee_bps if order.tif == "post_only" else spec.taker_fee_bps
            return Fill(order.venue, order.symbol, order.side, order.qty_base, px, fee_bps=fee,
                        order_id=f"dry-{int(time.time() * 1000)}")
        return venue.place(order, spec)

    def positions(self) -> dict[str, VenuePosition]:
        if self.dry_run:
            return {}
        merged: dict[str, VenuePosition] = {}
        for venue in (self.okx, self.lighter):
            try:
                merged.update({f"{venue.name}:{k}": v for k, v in venue.positions().items()})
            except Exception as exc:  # pragma: no cover - network
                log.warning("positions(%s) failed: %s", venue.name, exc)
        return merged

    def equities(self) -> tuple[float, float]:
        if self.dry_run:
            return 10_000.0, 10_000.0
        try:
            okx_eq = self.okx.equity_usd()
        except Exception:
            okx_eq = 0.0
        try:
            lt_eq = self.lighter.equity_usd()
        except Exception:
            lt_eq = 0.0
        return okx_eq, lt_eq


@dataclass
class MakerQuote:
    """A resting post-only order we own and must watch."""

    symbol: str
    venue: str
    side: str
    px: float
    qty_base: float
    placed_ts: float = field(default_factory=time.time)
    order_id: str = ""
    filled: bool = False
    fill_ts: float = 0.0
    fill_px: float = 0.0

    @property
    def age_s(self) -> float:
        return time.time() - self.placed_ts


class TwinEngine:
    def __init__(self, cfg: AppConfig, okx: Optional[OkxVenue] = None,
                 lighter: Optional[LighterVenue] = None, proxy: Optional[str] = None):
        self.cfg = cfg
        self.okx = okx or OkxVenue(cfg.okx, proxy=proxy)
        self.lighter = lighter or LighterVenue(cfg.lighter, proxy=proxy)
        self.fees: FeeBook = build_fee_book(cfg.venues)
        self.mech: MechanicsBook = build_mechanics(cfg.venues)
        self.broker = Broker(self.okx, self.lighter, dry_run=cfg.dry_run)
        self.states: dict[str, SymbolState] = {}
        self.exits = ExitQueue()
        self.maker_quotes: list[MakerQuote] = []
        self.wave: Optional[WaveTrader] = None
        self.ledger = PositionLedger(str(Path(cfg.state_dir) / "positions.json"))
        # Positions that exist on a venue but have no in-memory round.  They appear
        # after a restart / crash / OOM: the process has no memory of the round,
        # so without this the engine believes FLAT and quotes a *second* entry on
        # top of a live position.
        self.orphans = {}
        # symbol -> (until_ts, reason): hedge-failure circuit breaker
        self.benched: dict[str, tuple[float, str]] = {}
        self.hedge_fail_streak: dict[str, int] = {}
        self.trades: list[dict] = []
        self.alerts: list[dict] = []
        self._loaded = False
        self.rth_gates = {}

    # ------------------------------------------------------------------ universe
    def load_universe(self, approved: Optional[list[dict]] = None) -> dict[str, SymbolState]:
        entries = approved if approved is not None else self.cfg.symbols
        okx_specs = self.okx.specs()
        lighter_specs = self.lighter.specs()
        for entry in entries:
            symbol = str(entry.get("symbol", "")).upper()
            if not symbol or symbol not in okx_specs or symbol not in lighter_specs:
                log.warning("skipping %s: not listed on both venues", symbol or "<blank>")
                continue
            ratio = float(entry.get("unit_ratio") or 0.0)
            if ratio <= 0:
                log.warning("skipping %s: unit ratio not calibrated (run `arbtwin screen`)", symbol)
                continue
            session = Session.from_config(entry.get("session"))
            self.states[symbol] = SymbolState(
                symbol=symbol,
                unit_ratio=ratio,
                sigma_1h_bps=float(entry.get("sigma_1h_bps") or 0.0),
                basis_sigma_bps=float(entry.get("basis_sigma_bps") or 0.0),
                band_override_bps=float(entry.get("band_override_bps") or 0.0),
                wave_params=WaveParams(
                    entry_bps=float(entry.get("entry_bps_p95") or entry.get("band_override_bps") or 14.0),
                    exit_bps=float(entry.get("exit_bps_p50") or 4.5),
                    max_hold_h=float(entry.get("max_hold_h") or self.cfg.limits.max_hold_h),
                    entry_deadline_s=float(entry.get("entry_deadline_s") or 5400.0),
                    exit_deadline_s=float(entry.get("exit_deadline_s") or 3600.0),
                    max_unhedged_notional_usd=float(entry.get("max_unhedged_notional_usd")
                                                    or self.cfg.limits.max_unhedged_notional_usd),
                    leg_sigma_1h_bps=float(entry.get("sigma_1h_bps") or 0.0),
                ) if str(entry.get("entry_bps_p95") or entry.get("band_override_bps")) else None,
                spec_okx=okx_specs[symbol],
                spec_lighter=lighter_specs[symbol],
                session=session,
            )
        # The RTH net gate is an arm-time (live) veto. Dry-run keeps observing so
        # mechanics/replay tests and screening diagnostics still run.
        self.rth_gates = ({} if self.cfg.dry_run else
                          gate_universe(self.cfg, lambda m: self._alert("info", "rth", m)))
        self._reconcile_orphans()
        if self.cfg.strategy == "wave":
            params = {s: st.wave_params for s, st in self.states.items() if st.wave_params}
            if not params and self.states:
                self._alert("critical", "engine",
                            "strategy=wave but no symbol carries entry_bps_p95/band_override_bps "
                            "— the engine would only observe. Re-run `arbtwin screen` or set the "
                            "thresholds in config/symbols*.yaml.")
            if params:
                self.wave = WaveTrader(params, place=self._wave_place, cancel=self._wave_cancel,
                                       persist=lambda rec: write_state(f"{self.cfg.state_dir}/rounds.jsonl", rec),
                                       simulate_fills=self.cfg.dry_run,
                                       on_error=lambda msg: self._alert("warn", "wave", msg))
                for symbol in params:
                    self.states[symbol].band_override_bps = params[symbol].entry_bps
        self._loaded = True
        return self.states

    def start_streams(self) -> None:
        symbols = list(self.states)
        if not symbols:
            return
        for venue in (self.okx, self.lighter):
            try:
                venue.start(symbols)
            except Exception as exc:  # pragma: no cover - network
                self._alert("warn", venue.name, f"stream start failed: {exc}")

    # ------------------------------------------------------------------ gates
    @staticmethod
    def _direction_hint(okx_book: Book, lt_book: Book, unit_ratio: float) -> str:
        if unit_ratio <= 0 or not okx_book.mid or not lt_book.mid:
            return "flat"
        return ("long_okx_short_lighter"
                if (lt_book.mid / unit_ratio) >= okx_book.mid
                else "long_lighter_short_okx")

    @staticmethod
    def _net_carry_apr(direction: str, okx_stats: MarketStats, lt_stats: MarketStats) -> float:
        """Annualised funding cost of the intended position; positive = you pay.

        long_okx_short_lighter  -> you pay OKX funding, receive Lighter funding
        long_lighter_short_okx  -> the opposite
        """
        if direction == "long_okx_short_lighter":
            return okx_stats.funding_apr_pct - lt_stats.funding_apr_pct
        if direction == "long_lighter_short_okx":
            return lt_stats.funding_apr_pct - okx_stats.funding_apr_pct
        return 0.0

    def _incentive_bps(self) -> float:
        value = 0.0
        for venue in ("okx", "lighter"):
            fee_cfg = ((self.cfg.venues.get("venues") or {}).get(venue) or {}).get("fee") or {}
            value += float(fee_cfg.get("incentive_value_bps", 0.0) or 0.0)
        return value

    def _gate_symbol(self, st: SymbolState, okx_book: Book, lt_book: Book,
                     okx_stats: MarketStats, lt_stats: MarketStats) -> GateVerdict:
        limits = self.cfg.limits
        verdicts = [
            evaluate_venue(self.mech.get(OKX), okx_stats, okx_book, None, limits),
            evaluate_venue(self.mech.get(LIGHTER), lt_stats, lt_book, None, limits),
        ]
        merged = GateVerdict(allow=True)
        for v in verdicts:
            merged = merged.merge(v)

        # P1-3 was documented but never wired: `AnchorBook` was imported and never
        # instantiated, so `session: us_equity` had zero effect and the engine
        # would happily quote a closed-market perp -- exactly the regime where the
        # venue book is measured as frozen (stale 0.986, ~53 updates/120s) and the
        # only fill you get is the reopen gap.  Block entries outside the session.
        now = time.time()
        session_open = st.session.is_open(now)
        merged.metrics["session_open"] = session_open
        if not session_open:
            merged.allow = False
            merged.level = "block"
            merged.reasons.append(
                f"{st.symbol}: reference session closed ({st.session.kind}) — anchor frozen, "
                f"entries disabled (exits unaffected)")

        # per-leg funding is reported here and gated on the *intended* direction
        # after pricing (a hot leg is only expensive if you end up paying it)
        merged.metrics["funding_okx_apr"] = round(okx_stats.funding_apr_pct, 1)
        merged.metrics["funding_lighter_apr"] = round(lt_stats.funding_apr_pct, 1)

        card = build_card(st.symbol, [st.spec_okx, st.spec_lighter], [okx_stats, lt_stats],
                          (self.cfg.risk.get("registry") or {}))
        merged.metrics["risk_score"] = card.score
        merged.metrics["size_factor"] = card.size_factor
        if not card.allow:
            merged.allow = False
            merged.reasons.extend(card.hard_flags)
        elif card.soft_flags:
            merged.reasons.extend(card.soft_flags)
            if merged.level == "ok":
                merged.level = "warn"

        man_okx = profile(okx_book, st.symbol, OKX, min_cost_usd=self.mech.get(OKX).min_move_cost_usd)
        man_lt = profile(lt_book, st.symbol, LIGHTER, min_cost_usd=self.mech.get(LIGHTER).min_move_cost_usd)
        merged.metrics["band_multiplier"] = round(max(man_okx.band_multiplier, man_lt.band_multiplier), 2)
        merged.metrics["latency_multiplier"] = round(max(man_okx.latency_multiplier, man_lt.latency_multiplier), 2)
        if man_okx.thin_reference or man_lt.thin_reference:
            merged.reasons.extend(man_okx.reasons + man_lt.reasons)
            if merged.level == "ok":
                merged.level = "warn"
        return merged

    # ------------------------------------------------------------------ snapshot
    def _fetch(self, symbol: str) -> tuple[str, dict]:
        """One symbol's four reads, run in a pool: a 12-symbol poll is 12 round trips."""
        out: dict = {}
        for name, fn in (("okx_book", self.okx.book), ("lt_book", self.lighter.book),
                         ("okx_stats", self.okx.stats), ("lt_stats", self.lighter.stats)):
            try:
                out[name] = fn(symbol)
            except Exception as exc:
                out[name] = exc
        return symbol, out

    def _fetch_all(self) -> dict[str, dict]:
        symbols = list(self.states)
        if len(symbols) <= 2:
            return dict(self._fetch(s) for s in symbols)
        with ThreadPoolExecutor(max_workers=min(8, len(symbols)), thread_name_prefix="snap") as pool:
            return {sym: data for sym, data in pool.map(self._fetch, symbols)}

    def snapshot(self) -> dict:
        if not self._loaded:
            self.load_universe()
        rows: list[SpreadRow] = []
        now = time.time()
        raw = self._fetch_all()
        for symbol, st in self.states.items():
            data = raw.get(symbol) or {}
            failed = [k for k, v in data.items() if isinstance(v, Exception)]
            if failed or len(data) < 4:
                reason = data.get(failed[0]) if failed else "no data"
                self._alert("warn", symbol, f"market data failed: {reason}")
                continue
            okx_book, lt_book = data["okx_book"], data["lt_book"]
            okx_stats, lt_stats = data["okx_stats"], data["lt_stats"]
            gate = self._gate_symbol(st, okx_book, lt_book, okx_stats, lt_stats)
            st.last_gate = gate

            fees = fee_from_market_specs(st.spec_okx, st.spec_lighter,
                                         incentive_value_bps=self._incentive_bps())
            direction = self._direction_hint(okx_book, lt_book, st.unit_ratio)
            carry = self._net_carry_apr(direction, okx_stats, lt_stats)
            # funding is a cost, not a veto: price the expected holding period in
            funding_cost_bps = max(carry, 0.0) / 100.0 / 8760.0 * 1e4 * self.cfg.limits.expected_hold_h
            latency_ms = (self.mech.get(LIGHTER).taker_speed_bump_ms) + int(self.cfg.risk.get("extra_latency_ms", 120))
            row = compute_row(
                symbol, okx_book, lt_book, st.unit_ratio, fees, st.sigma_1h_bps,
                self.cfg.band, sigma_basis_bps=st.basis_sigma_bps or None, latency_ms=latency_ms,
                slippage_bps=self.cfg.limits.max_slippage_bps * gate.metrics.get("latency_multiplier", 1.0),
                funding_cost_bps=funding_cost_bps,
                band_override_bps=st.band_override_bps,
            )
            side = "cost" if carry > 0.5 else ("income" if carry < -0.5 else "flat")
            row.reasons.append(
                f"net carry {carry:+.0f}% APR ({side}, {funding_cost_bps:.1f}bps over "
                f"{self.cfg.limits.expected_hold_h:.0f}h)")
            if row.action in ("ENTER", "MAKER") and carry > self.cfg.limits.max_funding_carry_apr_pct * 3:
                row.action = "BLOCK"
                row.reasons.append(
                    f"net carry {carry:.0f}% APR > 3x {self.cfg.limits.max_funding_carry_apr_pct:.0f}% limit")
            # widen the band for thin references (P2-7) and fold the gate verdict in
            row.entry_band_bps *= gate.metrics.get("band_multiplier", 1.0)
            if not gate.allow:
                row.action = "BLOCK"
                row.reasons.extend(gate.reasons)
            elif row.action == "ENTER" and row.net_edge_bps < row.entry_band_bps:
                row.action = "WAIT"
            st.last_row = row
            rows.append(row)
        summary = summarise(rows)
        return {
            "ts": now,
            "rows": rows,
            "summary": summary,
            "gates": {s: (st.last_gate.metrics if st.last_gate else {}) for s, st in self.states.items()},
            "positions": {k: v.__dict__ for k, v in self.broker.positions().items()},
            "exits": [t.__dict__ for t in self.exits.open_tickets()],
        }

    # ------------------------------------------------------------------ execution
    def execute_best(self, snap: dict) -> list[dict]:
        rows: list[SpreadRow] = snap["rows"]
        self._poll_maker(rows)
        maker_rows = [r for r in rows if r.action == "MAKER" and self._can_quote(r.symbol)]
        done = [self._quote_maker(r) for r in maker_rows[: self.cfg.limits.max_open_symbols]]
        candidates = [r for r in rows if r.action == "ENTER"]
        if not candidates:
            return done
        candidates.sort(key=lambda r: -r.net_edge_bps)
        limit_positions = self.cfg.limits.max_open_symbols
        for row in candidates:
            if len([s for s in self.states.values() if s.side != "flat"]) >= limit_positions:
                break
            st = self.states[row.symbol]
            if time.time() < st.cooldown_until:
                continue
            plan = self._size_plan(row, st)
            if plan is None:
                continue
            done.append(self._execute_plan(plan, st))
        return done

    def _size_plan(self, row: SpreadRow, st: SymbolState, mode: str = "hedge_now") -> Optional[ExecutionPlan]:
        limits = self.cfg.limits
        okx_eq, lt_eq = self.broker.equities()
        if okx_eq <= 0 or lt_eq <= 0:
            self._alert("warn", st.symbol, "no equity on one venue — cannot size (dry_run gives a fictional 10k each)")
            return None
        okx_book = self.okx.book(st.symbol)
        lt_book = self.lighter.book(st.symbol)
        size_factor = st.last_gate.metrics.get("size_factor", 1.0) if st.last_gate else 1.0
        notional_okx = max_position_usd(okx_eq, min(st.spec_okx.max_leverage, limits.target_leverage),
                                       limits.max_margin_usage_pct,
                                       okx_book.depth_usd(100.0), hard_cap_usd=limits.max_notional_usd)
        notional_lt = max_position_usd(lt_eq, min(st.spec_lighter.max_leverage, limits.target_leverage),
                                       limits.max_margin_usage_pct,
                                       lt_book.depth_usd(100.0), hard_cap_usd=limits.max_notional_usd)
        notional = min(notional_okx, notional_lt) * size_factor
        if notional < 25.0:
            self._alert("warn", st.symbol, f"sized notional ${notional:.0f} below the $25 minimum")
            return None
        return build_plan(row, okx_book, lt_book, st.unit_ratio, notional,
                          max_unhedged_ms=limits.max_unhedged_ms, mode=mode)

    def _execute_plan(self, plan: ExecutionPlan, st: SymbolState) -> dict:
        """leading (Lighter post-only) -> confirm -> hedge (OKX IOC) -> or unwind."""
        started = time.time()
        spec_lead = st.spec_lighter
        spec_hedge = st.spec_okx
        lead_fill = self.broker.place(plan.leading, spec_lead)
        hedged = False
        hedge_fill: Optional[Fill] = None
        for attempt in range(3):
            slip = self.cfg.limits.max_slippage_bps * (attempt + 1)
            px = plan.hedge.px * (1 + slip / 1e4 if plan.hedge.side == "buy" else 1 - slip / 1e4)
            hedge_order = OrderRequest(plan.hedge.venue, plan.hedge.symbol, plan.hedge.side,
                                       plan.hedge.qty_base, px, order_type="limit", tif="ioc")
            try:
                hedge_fill = self.broker.place(hedge_order, spec_hedge)
                hedged = True
                break
            except Exception as exc:
                self._alert("warn", st.symbol, f"hedge attempt {attempt + 1} failed: {exc}")
                time.sleep(0.05)
        elapsed_ms = (time.time() - started) * 1000

        if not hedged or hedge_fill is None:
            streak = self.hedge_fail_streak.get(st.symbol, 0) + 1
            self.hedge_fail_streak[st.symbol] = streak
            if streak >= self.cfg.limits.max_hedge_failures:
                until = time.time() + self.cfg.limits.hedge_failure_cooldown_s
                self.benched[st.symbol] = (until, f"{streak} consecutive hedge failures")
                self.hedge_fail_streak[st.symbol] = 0
                self._alert("critical", st.symbol,
                            f"hedge failed {streak}x in a row — symbol benched "
                            f"{self.cfg.limits.hedge_failure_cooldown_s:.0f}s")
            # never leave the leading leg naked: unwind reduce-only
            try:
                unwind = OrderRequest(plan.leading.venue, plan.leading.symbol,
                                      "buy" if plan.leading.side == "sell" else "sell",
                                      plan.leading.qty_base,
                                      plan.leading.px, order_type="limit", tif="ioc", reduce_only=True)
                self.broker.place(unwind, spec_lead)
            except Exception as exc:
                self._alert("critical", st.symbol, f"UNWIND FAILED, position is naked: {exc}")
            self._alert("critical", st.symbol, f"hedge failed after {elapsed_ms:.0f}ms — leading leg unwound")
            st.cooldown_until = time.time() + self.cfg.limits.cooldown_after_fill_s
            return {"symbol": st.symbol, "status": "unwound", "elapsed_ms": elapsed_ms}

        self.hedge_fail_streak[st.symbol] = 0
        if elapsed_ms > plan.max_unhedged_ms:
            self._alert("warn", st.symbol,
                        f"unhedged {elapsed_ms:.0f}ms > budget {plan.max_unhedged_ms}ms")

        st.side = "long_lighter" if plan.leading.side == "buy" else "short_lighter"
        st.entry_ts = time.time()
        st.entry_lighter = lead_fill.px
        st.entry_okx = hedge_fill.px
        st.qty_lighter = lead_fill.qty_base * (1 if plan.leading.side == "buy" else -1)
        st.qty_okx = hedge_fill.qty_base * (1 if hedge_fill.side == "buy" else -1)
        st.cooldown_until = time.time() + self.cfg.limits.cooldown_after_fill_s
        trade = {
            "ts": st.entry_ts, "symbol": st.symbol, "side": plan.direction,
            "lighter_px": lead_fill.px, "okx_px": hedge_fill.px,
            "qty_lighter": lead_fill.qty_base, "qty_okx": hedge_fill.qty_base,
            "expected_net_bps": plan.expected_net_bps, "unhedged_ms": round(elapsed_ms, 1),
            "status": "open",
        }
        self.trades.append(trade)
        self._persist_trade(trade)
        return trade

    # ------------------------------------------------------------------ maker mode
    def _can_quote(self, symbol: str) -> bool:
        st = self.states[symbol]
        if st.side != "flat":
            return False
        return not any(q.symbol == symbol for q in self.maker_quotes)

    def _quote_maker(self, row: SpreadRow) -> dict:
        """Post both legs post-only.  Cost = maker fees only; risk = one-sided fills."""
        st = self.states[row.symbol]
        plan = self._size_plan(row, st, mode="dual_maker")
        if plan is None:
            return {"symbol": row.symbol, "status": "no_size"}
        placed = []
        for order, spec in ((plan.leading, st.spec_lighter), (plan.hedge, st.spec_okx)):
            quote = MakerQuote(symbol=row.symbol, venue=order.venue, side=order.side,
                               px=order.px, qty_base=order.qty_base)
            if not self.cfg.dry_run:
                try:
                    fill = self.broker.place(order, spec)
                    quote.order_id = fill.order_id
                except Exception as exc:
                    self._alert("warn", row.symbol, f"maker order rejected: {exc}")
                    continue
            self.maker_quotes.append(quote)
            placed.append({"venue": order.venue, "side": order.side, "px": round(order.px, 6),
                           "qty": round(order.qty_base, 6)})
        self._alert("info", row.symbol,
                    f"maker quotes posted @ {row.maker_net_bps:.1f}bps (band {row.maker_band_bps:.1f}bps)")
        return {"symbol": row.symbol, "status": "quoted", "mode": "dual_maker", "quoted": placed}

    def _poll_maker(self, rows: list[SpreadRow]) -> None:
        """Detect fills, cancel stale quotes, escalate one-sided fills to exits."""
        if not self.maker_quotes:
            return
        now = time.time()
        books: dict[tuple[str, str], Book] = {}
        for q in list(self.maker_quotes):
            if q.filled:
                continue
            key = (q.venue, q.symbol)
            if key not in books:
                try:
                    books[key] = (self.lighter.book(q.symbol) if q.venue == LIGHTER
                                  else self.okx.book(q.symbol))
                except Exception:
                    continue
            book = books[key]
            if not book.bids or not book.asks:
                continue
            crossed = (q.side == "buy" and book.ask <= q.px) or (q.side == "sell" and book.bid >= q.px)
            if crossed:
                q.filled, q.fill_ts, q.fill_px = True, now, q.px
            elif q.age_s > self.cfg.limits.max_quote_ttl_s:
                if not self.cfg.dry_run:
                    venue = self.lighter if q.venue == LIGHTER else self.okx
                    venue.cancel(q.symbol, q.order_id)
                self.maker_quotes.remove(q)

        for symbol in {q.symbol for q in self.maker_quotes}:
            legs = [q for q in self.maker_quotes if q.symbol == symbol]
            filled = [q for q in legs if q.filled]
            if len(filled) == len(legs) and len(legs) == 2:
                row = next((r for r in rows if r.symbol == symbol), None)
                self._open_from_maker(symbol, filled, row.maker_net_bps if row else None)
                self.maker_quotes = [q for q in self.maker_quotes if q.symbol != symbol]
            elif filled:
                oldest_fill = min(q.fill_ts for q in filled)
                budget = self.cfg.limits.max_unhedged_ms / 1000.0
                if now - oldest_fill > budget:
                    q = filled[0]
                    self._alert("critical", symbol,
                                f"one-sided maker fill on {q.venue} unhedged "
                                f"{(now - oldest_fill) * 1000:.0f}ms — queuing passive exit")
                    st = self.states[symbol]
                    st.side = f"{'long' if q.side == 'buy' else 'short'}_{'lighter' if q.venue == LIGHTER else 'okx'}"
                    st.entry_ts = q.fill_ts
                    st.entry_okx = q.fill_px if q.venue == OKX else 0.0
                    st.entry_lighter = q.fill_px if q.venue == LIGHTER else 0.0
                    st.qty_okx = q.qty_base if (q.venue == OKX and q.side == "buy") else \
                        (-q.qty_base if q.venue == OKX else 0.0)
                    st.qty_lighter = q.qty_base / st.unit_ratio if (q.venue == LIGHTER and q.side == "buy") else \
                        (-q.qty_base / st.unit_ratio if q.venue == LIGHTER else 0.0)
                    self.exits.add(ExitTicket(symbol=symbol, venue=q.venue,
                                              side="sell" if q.side == "buy" else "buy",
                                              qty_base=q.qty_base, remaining=q.qty_base))
                    for other in legs:
                        if not other.filled and not self.cfg.dry_run:
                            (self.lighter if other.venue == LIGHTER else self.okx).cancel(
                                other.symbol, other.order_id)
                    self.maker_quotes = [x for x in self.maker_quotes if x.symbol != symbol]

    def _open_from_maker(self, symbol: str, fills: list[MakerQuote],
                         expected_net_bps: float | None = None) -> None:
        st = self.states[symbol]
        lt = next((q for q in fills if q.venue == LIGHTER), None)
        ox = next((q for q in fills if q.venue == OKX), None)
        if not lt or not ox:
            return
        st.side = "long_lighter" if lt.side == "buy" else "short_lighter"
        st.entry_ts = max(lt.fill_ts, ox.fill_ts)
        st.entry_lighter = lt.fill_px
        st.entry_okx = ox.fill_px
        st.qty_lighter = lt.qty_base * (1 if lt.side == "buy" else -1)
        st.qty_okx = ox.qty_base * (1 if ox.side == "buy" else -1)
        trade = {"ts": st.entry_ts, "symbol": symbol, "side": st.side, "mode": "dual_maker",
                 "lighter_px": lt.fill_px, "okx_px": ox.fill_px,
                 "qty_lighter": abs(st.qty_lighter), "qty_okx": abs(st.qty_okx),
                 "expected_net_bps": expected_net_bps, "status": "open"}
        self.trades.append(trade)
        self._persist_trade(trade)
        self._alert("info", symbol, "dual-maker both legs filled — position opened")

    # ------------------------------------------------------------------ reconciliation
    def _reconcile_orphans(self) -> None:
        """Read venue positions at startup; anything open is an orphan until flat."""
        try:
            positions = self.broker.positions()
        except Exception as exc:
            self._alert("critical", "engine", f"cannot read venue positions at startup: {exc}")
            return
        for _key, pos in positions.items():
            if abs(getattr(pos, "qty_base", 0.0)) > 1e-12:
                rnd = self.wave.rounds.get(pos.symbol) if self.wave else None
                if rnd is not None and rnd.state is not RoundState.FLAT:
                    continue        # our own open round, not an orphan
                self.orphans[pos.symbol] = pos
                self._alert("critical", pos.symbol,
                            f"orphan position at startup: {pos.venue} {pos.qty_base:+.6f} "
                            f"@ {pos.avg_entry} — entries blocked until flat")
        if not positions and not self.cfg.dry_run:
            # a flat account is the normal case, not a warning
            self._alert("info", "engine", "startup position check: flat (no open positions)")

    def _update_orphans(self, snap: dict) -> None:
        """Clear orphans once both legs are flat again (cheap: reuses the snapshot)."""
        if not self.orphans:
            return
        live: dict[str, float] = {}
        for _key, pos in (snap.get("positions") or {}).items():
            symbol = pos.get("symbol") or _key.split(":")[-1]
            live[symbol] = live.get(symbol, 0.0) + abs(float(pos.get("qty_base") or 0.0))
        for symbol in list(self.orphans):
            if live.get(symbol, 0.0) <= 1e-12:
                self.orphans.pop(symbol, None)
                self._alert("info", symbol, "orphan cleared: both legs flat")

    # ------------------------------------------------------------------ wave mode
    def _wave_place(self, order: OrderRequest) -> Fill:
        st = self.states.get(order.symbol)
        if st is None:
            raise VenueError(f"unknown symbol {order.symbol}")
        spec = st.spec_lighter if order.venue == LIGHTER else st.spec_okx
        if self.cfg.dry_run:
            fee = spec.maker_fee_bps if order.tif == "post_only" else spec.taker_fee_bps
            return Fill(order.venue, order.symbol, order.side, order.qty_base, order.px,
                        fee_bps=fee, order_id=f"dry-{int(time.time() * 1000) % 100000}")
        return self.broker.place(order, spec)

    def _wave_cancel(self, symbol: str, venue: str, order_id: str) -> bool:
        if self.cfg.dry_run or not order_id:
            return True
        try:
            target = self.lighter if venue == LIGHTER else self.okx
            return bool(target.cancel(symbol, order_id))
        except Exception:
            return False

    def _target_notional(self, symbol: str) -> float:
        st = self.states[symbol]
        limits = self.cfg.limits
        okx_eq, lt_eq = self.broker.equities()
        if okx_eq <= 0 or lt_eq <= 0:
            return 0.0
        okx_book = self.okx.book(symbol)
        lt_book = self.lighter.book(symbol)
        size_factor = st.last_gate.metrics.get("size_factor", 1.0) if st.last_gate else 1.0
        lev_okx = min(st.spec_okx.max_leverage, limits.target_leverage)
        lev_lt = min(st.spec_lighter.max_leverage, limits.target_leverage)
        a = max_position_usd(okx_eq, lev_okx, limits.max_margin_usage_pct,
                            okx_book.depth_usd(100.0), hard_cap_usd=limits.max_notional_usd)
        b = max_position_usd(lt_eq, lev_lt, limits.max_margin_usage_pct,
                            lt_book.depth_usd(100.0), hard_cap_usd=limits.max_notional_usd)
        notional = max(0.0, min(a, b) * size_factor)
        # Lighter rejects anything below its `min_quote_amount` ($10 on every
        # perp today).  With a tiny account, sizing can fall under it -> skip the
        # round instead of spraying rejects at the venue.
        venue_min = max(st.spec_lighter.min_quote or 0.0, st.spec_okx.min_quote or 0.0)
        floor = max(venue_min, getattr(limits, "min_round_notional_usd", 0.0) or 0.0)
        if floor and notional < floor:
            self._alert("warn", symbol,
                        f"sizing ${notional:.2f} below the round floor ${floor:.2f} "
                        f"(equity okx={okx_eq:.2f}/lighter={lt_eq:.2f})")
            return 0.0
        return notional

    def wave_step(self, snap: dict) -> list[dict]:
        """One poll of the round state machine for every symbol in the universe."""
        if self.wave is None:
            return []
        out: list[dict] = []
        for row in snap["rows"]:
            st = self.states.get(row.symbol)
            if st is None or st.wave_params is None or not self.wave.params.get(row.symbol):
                continue
            gate = self.rth_gates.get(row.symbol)
            if gate is not None and not gate.allow:
                out.append({"symbol": row.symbol, "blocked": gate.reason})
                continue
            rnd = self.wave.round_for(row.symbol)
            holding = rnd.state is not RoundState.FLAT
            # A gate/notional failure may veto a *new* entry, but it must never
            # stop the management of a position we already hold (exits, re-pegs,
            # entry/exit deadlines).  Otherwise one REST hiccup parks the position
            # with resting orders and no deadline for as long as the outage lasts.
            until, why = self.benched.get(row.symbol, (0.0, ""))
            if until:
                if time.time() < until:
                    out.append({"symbol": row.symbol, "blocked": f"benched: {why}"})
                    continue
                self.benched.pop(row.symbol, None)
                self._alert("info", row.symbol, f"bench expired ({why}) — entries re-enabled")
            if not holding:
                if row.symbol in self.orphans:
                    out.append({"symbol": row.symbol, "blocked": "orphan position on venue"})
                    continue
                if not st.last_gate or not st.last_gate.allow:
                    continue
            try:
                okx_book = self.okx.book(row.symbol)
                lt_book = self.lighter.book(row.symbol)
            except Exception as exc:
                self._alert("warn", row.symbol, f"wave: book unavailable: {exc}")
                if rnd.state is not RoundState.FLAT:
                    self._alert("critical", row.symbol,
                                f"force-flat: book unavailable while holding position ({rnd.state.value})")
                    payload = self.wave.force_flat(row.symbol, f"book unavailable: {exc}")
                    out.append(payload)
                continue
            if self.cfg.dry_run:
                fills = self.wave.poll_fills(row.symbol, okx_book, lt_book)
            else:
                fills = []          # filled by _reconcile_positions() before this step
            if holding:
                notional = 0.0        # not consumed by ENTRY_PARTIAL/HEDGED/EXIT states
            else:
                notional = self._target_notional(row.symbol)
                if notional < 25.0:
                    continue
            qty_okx = notional / max(okx_book.mid, 1e-12) if notional else 0.0
            payload = self.wave.step(row.symbol, row.basis_bps, okx_book, lt_book,
                                     st.unit_ratio, qty_okx, row.maker_net_bps,
                                     minutes_to_close=st.session.minutes_to_close(time.time()))
            payload["fills"] = fills
            if payload["executed"] or fills:
                out.append(payload)
        return out

    # ------------------------------------------------------------------ risk loop
    def manage_risk(self, snap: dict) -> list[dict]:
        actions: list[dict] = []
        limits = self.cfg.limits
        for symbol, st in self.states.items():
            if st.side == "flat":
                continue
            if abs(self._position_lighter(st)) < 1e-12 and abs(self._position_okx(st)) < 1e-12:
                continue
            row = st.last_row
            if not row:
                continue
            mark_okx = self.okx.stats(symbol).mark_price
            mark_lt = self.lighter.stats(symbol).mark_price
            # combined pnl in bps on notional (delta-neutral so this is basis capture)
            if st.entry_okx > 0 and st.entry_lighter > 0 and st.unit_ratio > 0:
                pnl_bps = ((mark_lt / st.unit_ratio) - mark_okx) / mark_okx * 1e4 \
                    - ((st.entry_lighter / st.unit_ratio) - st.entry_okx) / st.entry_okx * 1e4
            else:
                pnl_bps = 0.0
            # adverse single-leg move (the thing that actually hurts)
            drift_okx = abs(mark_okx - st.entry_okx) / st.entry_okx if st.entry_okx else 0.0
            drift_lt = abs(mark_lt - st.entry_lighter) / st.entry_lighter if st.entry_lighter else 0.0
            held_h = (time.time() - st.entry_ts) / 3600.0 if st.entry_ts else 0.0
            reason = None
            if held_h >= limits.max_hold_h:
                reason = f"max hold {held_h:.1f}h reached (basis reverts fast; do not babysit)"
            elif max(drift_okx, drift_lt) >= limits.emergency_stop_loss_pct:
                reason = f"single-leg drift {max(drift_okx, drift_lt):.1%} >= {limits.emergency_stop_loss_pct:.0%}"
            elif pnl_bps >= exit_band_bps(row.entry_band_bps, self.cfg.band):
                reason = f"basis captured {pnl_bps:.1f}bps >= exit band"
            if reason:
                actions.append(self._open_exit(st, reason, forced=("drift" in reason)))
        # step the queue
        for ticket in self.exits.open_tickets():
            book = self.lighter.book(ticket.symbol) if ticket.venue == LIGHTER else self.okx.book(ticket.symbol)
            order = self.exits.plan(ticket, book)
            if not order:
                continue
            spec = self.states[ticket.symbol].spec_lighter if ticket.venue == LIGHTER else self.states[ticket.symbol].spec_okx
            try:
                fill = self.broker.place(order, spec)
                ticket.fill(fill.qty_base)
                actions.append({"symbol": ticket.symbol, "venue": ticket.venue, "exit_fill": fill.qty_base,
                                "px": fill.px, "remaining": ticket.remaining})
                if ticket.done:
                    st = self.states[ticket.symbol]
                    st.side = "flat"
                    st.entry_okx = st.entry_lighter = 0.0
            except Exception as exc:
                self._alert("warn", ticket.symbol, f"exit order failed: {exc}")
        return actions

    def _open_exit(self, st: SymbolState, reason: str, forced: bool = False) -> dict:
        """Queue passive exits for whatever legs actually exist.

        Written around positions rather than around `st.side`, because the
        interesting failure mode is a *single* leg (one-sided maker fill, hedge
        rejected) and that leg still has to be unwound with the correct side.
        """
        lt_qty = self._position_lighter(st)
        ox_qty = self._position_okx(st)
        queued = 0
        if abs(lt_qty) > 1e-12:
            self.exits.add(ExitTicket(symbol=st.symbol, venue=LIGHTER,
                                      side="sell" if lt_qty > 0 else "buy",
                                      qty_base=abs(lt_qty), remaining=abs(lt_qty),
                                      forced=forced))
            queued += 1
        if abs(ox_qty) > 1e-12:
            self.exits.add(ExitTicket(symbol=st.symbol, venue=OKX,
                                      side="sell" if ox_qty > 0 else "buy",
                                      qty_base=abs(ox_qty), remaining=abs(ox_qty),
                                      forced=forced))
            queued += 1
        level = "warn" if not forced else "critical"
        self._alert(level, st.symbol, f"exit queued ({queued} leg(s)): {reason}")
        return {"symbol": st.symbol, "exit_queued": reason, "legs": queued, "forced": forced}

    def _position_lighter(self, st: SymbolState) -> float:
        # in dry run the engine tracks its own book; live reads venue positions
        if self.broker.dry_run:
            return st.qty_lighter
        return self.broker.positions().get(f"lighter:{st.symbol}", VenuePosition(LIGHTER, st.symbol, 0.0)).qty_base

    def _position_okx(self, st: SymbolState) -> float:
        if self.broker.dry_run:
            return st.qty_okx
        return self.broker.positions().get(f"okx:{st.symbol}", VenuePosition(OKX, st.symbol, 0.0)).qty_base

    # ------------------------------------------------------------------ plumbing
    def _alert(self, level: str, scope: str, message: str) -> None:
        item = {"ts": time.time(), "level": level, "scope": scope, "message": message}
        self.alerts.append(item)
        log.log({"warn": logging.WARNING, "critical": logging.ERROR}.get(level, logging.INFO),
                "[%s] %s", scope, message)
        write_state(f"{self.cfg.state_dir}/alerts.jsonl", item)

    def _persist_trade(self, trade: dict) -> None:
        write_state(f"{self.cfg.state_dir}/trades.jsonl", trade)

    def _kill_switch(self) -> None:
        """Cancel all orders, force-flat all open rounds, log kill event."""
        self._alert("critical", "engine", "KILL SWITCH ACTIVATED — cancelling all, force-flat")
        # Cancel everything on both venues
        for symbol in self.states:
            for venue in (self.okx, self.lighter):
                try:
                    venue.cancel_all(symbol)
                except Exception:
                    pass
        # Force-flat every open wave round
        if self.wave is not None:
            for symbol, rnd in list(self.wave.rounds.items()):
                if rnd.state is not RoundState.FLAT:
                    try:
                        self.wave.force_flat(symbol, "kill switch")
                    except Exception as exc:
                        self._alert("critical", symbol, f"kill force-flat failed: {exc}")
        write_state(f"{self.cfg.state_dir}/kill.jsonl", {
            "ts": time.time(), "reason": "KILL file or ARBTWIN_KILL env"
        })

    # ------------------------------------------------------------------ loop
    def run_once(self) -> dict:
        # Kill switch: state/KILL file or ARBTWIN_KILL=1 env var
        kill_path = Path(self.cfg.state_dir) / "KILL"
        if kill_path.exists() or os.environ.get("ARBTWIN_KILL") == "1":
            self._kill_switch()
            return {"ts": time.time(), "killed": True}

        snap = self.snapshot()
        self._update_orphans(snap)
        if not self.cfg.dry_run and self.wave is not None:
            self._reconcile_positions()
        if self.cfg.strategy == "wave":
            executed = self.wave_step(snap)
            risk_actions = []
        else:
            executed = self.execute_best(snap)
            risk_actions = self.manage_risk(snap)
        payload = {
            "ts": snap["ts"],
            "symbols": [
                {
                    "symbol": r.symbol, "action": r.action, "direction": r.direction,
                    "basis_bps": round(r.basis_bps, 2), "net_bps": round(r.net_edge_bps, 2),
                    "band_bps": round(r.entry_band_bps, 2), "fee_bps": round(r.fee_bps, 2),
                    "latency_bps": round(r.latency_cost_bps, 2),
                    # spread tape: both venues' own quoted spreads, so the
                    # "average spread by session" question can be answered from
                    # our own recordings (no venue publishes spread history)
                    "okx_spread_bps": round(getattr(r, "okx_spread_bps", 0.0), 2),
                    "lt_spread_bps": round(getattr(r, "lt_spread_bps", 0.0), 2),
                    "spread_cost_bps": round(getattr(r, "spread_cost_bps", 0.0), 2),
                    "reasons": r.reasons[:3],
                }
                for r in snap["rows"]
            ],
            "executed": executed,
            "risk_actions": risk_actions,
            "dry_run": self.cfg.dry_run,
        }
        write_state(f"{self.cfg.state_dir}/snapshot.jsonl", payload)
        return payload

    def _assert_live_is_safe(self) -> None:
        """Live is allowed only when fills come from the venue, not from the book.

        `ARBTWIN_FILL_SOURCE=positions` switches the wave round to position-delta
        reconciliation (arbtwin/reconcile.py).  Anything else keeps live disabled:
        a live run whose fills are inferred from book crossings invents PnL and
        desynchronises the round from the account.
        """
        if self.cfg.dry_run:
            return
        if getattr(self.cfg, "fill_source", "") == "positions":
            if not self.cfg.okx.has_private:
                raise RuntimeError("live needs OKX credentials (API key/secret/passphrase)")
            if not self.cfg.lighter.has_private:
                raise RuntimeError("live needs Lighter API key index + private key + account index")
            return
        raise RuntimeError(
            "live trading needs fills from the venue: set ARBTWIN_FILL_SOURCE=positions "
            "(position-delta reconciliation). The book-crossing proxy is dry-run only."
        )

    def _cancel_stale_orders(self) -> None:
        """A restart must not leave yesterday's GTC orders live on the venue."""
        if self.cfg.dry_run:
            return
        for symbol in self.states:
            for venue in (self.okx, self.lighter):
                try:
                    n = venue.cancel_all(symbol)
                    if n:
                        self._alert("warn", symbol, f"cancelled {n} stale order(s) on {venue.name}")
                except Exception as exc:
                    self._alert("warn", symbol, f"cancel_all on {venue.name} failed: {exc}")

    def _reconcile_positions(self) -> list[FillEvent]:
        """Turn venue position deltas into wave fills (live only)."""
        if self.wave is None:
            return []
        try:
            positions = self.broker.positions()
        except Exception as exc:
            self._alert("warn", "engine", f"position poll failed: {exc}")
            return []
        events = self.ledger.reconcile(positions)
        for ev in events:
            rnd = self.wave.rounds.get(ev.symbol)
            if rnd is None:
                self._alert("warn", ev.symbol,
                            f"position delta without a round: {ev.venue} {ev.side} {ev.qty_base:.6f}")
                continue
            if ev.px <= 0:
                try:
                    book = self.okx.book(ev.symbol) if ev.venue == OKX else self.lighter.book(ev.symbol)
                    ev.px = book.mid
                except Exception:
                    pass
            from . import wave as _w
            intents = _w.on_fill(rnd, ev.venue, ev.qty_base, ev.px)
            for intent in intents:
                if intent.kind == "cancel" and intent.venue:
                    try:
                        target = self.lighter if intent.venue == LIGHTER else self.okx
                        target.cancel_all(ev.symbol)
                    except Exception:
                        pass
                    self._alert("warn", ev.symbol,
                                f"cancelled {intent.venue} leg: {intent.reason}")
            self._alert("info", ev.symbol,
                        f"fill reconciled: {ev.venue} {ev.side} {ev.qty_base:.6f} @ {ev.px:.6f} "
                        f"-> {rnd.state.value}")
        return events

    def run_forever(self, iterations: int = 0, interval_s: float | None = None) -> None:
        self._assert_live_is_safe()
        self._cancel_stale_orders()
        # adopt whatever the venues already hold: those units are not fills of
        # any round we are about to run.
        try:
            self.ledger.seed(self.broker.positions())
        except Exception as exc:
            self._alert("warn", "engine", f"position seed failed: {exc}")
        interval = (interval_s if interval_s is not None else self.cfg.poll_interval_ms / 1000.0)
        # rate-limit floor: each symbol costs ~2 REST calls per poll and
        # `max_open_symbols` is not the universe size — scale, never hammer.
        floor_s = 0.12 * max(1, len(self.states or self.cfg.symbols))
        if interval < floor_s:
            log.info("interval %.2fs below the safe floor for %d symbols; using %.2fs",
                     interval, len(self.states or self.cfg.symbols), floor_s)
            interval = floor_s
        i = 0
        self.load_universe()
        self.start_streams()
        try:
            while iterations <= 0 or i < iterations:
                i += 1
                try:
                    self.run_once()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # pragma: no cover - live loop
                    self._alert("warn", "engine", f"poll failed: {exc}")
                time.sleep(interval)
        finally:
            for venue in (self.okx, self.lighter):
                try:
                    venue.stop()
                except Exception:
                    pass