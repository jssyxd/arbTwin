"""P1-3  The pricing anchor: what is this thing actually worth right now?

On a CEX-only pair the mid is a decent anchor.  The moment one leg is a
tokenised equity, a pre-IPO claim, or a synthetic spot, the on-venue mid is a
*quote about a quote*: it lags the reference market, it is frozen when the
reference market is closed, and on thin books it is manufacturable.  The
observable hierarchy is therefore:

    tradfi spot / RFQ  >  median(venue index prices)  >  venue mid

and when the two venue indices disagree beyond tolerance we do **not** average
them into a fake fair value: we refuse to price at all.  Averaging two
disagreeing oracles is how market makers systematically buy the wrong one.

Session awareness is part of the anchor: outside the reference market's hours
the anchor is *frozen* to the last good value, flagged, and the engine is only
allowed to reduce inventory against it.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TZ_CACHE: dict[str, ZoneInfo] = {}


def _tz(name: str) -> ZoneInfo:
    """Lazy tz lookup: importing this module must not require tzdata (Windows)."""
    if name not in _TZ_CACHE:
        try:
            _TZ_CACHE[name] = ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - platform issue
            raise RuntimeError(
                f"timezone {name!r} unavailable — `pip install tzdata` (required on Windows)"
            ) from exc
    return _TZ_CACHE[name]


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    kind: str = "crypto"          # crypto | us_equity | uk_equity | commodities
    tz: str = "America/New_York"
    open_hhmm: tuple[int, int] = (9, 30)
    close_hhmm: tuple[int, int] = (16, 0)

    def is_open(self, ts: float) -> bool:
        if self.kind == "crypto":
            return True
        tz = _tz(self.tz)
        now = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).astimezone(tz)
        if now.weekday() >= 5:
            return False
        minutes = now.hour * 60 + now.minute
        return (self.open_hhmm[0] * 60 + self.open_hhmm[1]) <= minutes < (self.close_hhmm[0] * 60 + self.close_hhmm[1])

    def minutes_to_close(self, ts: float) -> float:
        """Minutes until the reference market closes (0 when closed)."""
        if self.kind == "crypto" or not self.is_open(ts):
            # 24/7 or already closed: there is no "close" to race against.  New
            # entries are blocked by the gate when closed; forcing an exit into a
            # dead book would be worse than holding.
            return float("inf")
        tz = _tz(self.tz)
        now = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).astimezone(tz)
        close = now.replace(hour=self.close_hhmm[0], minute=self.close_hhmm[1], second=0, microsecond=0)
        return max(0.0, (close - now).total_seconds() / 60.0)

    def minutes_to_open(self, ts: float) -> float:
        if self.is_open(ts):
            return 0.0
        tz = _tz(self.tz)
        now = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).astimezone(tz)
        for add_days in range(0, 5):
            day = now + _dt.timedelta(days=add_days)
            if day.weekday() >= 5:
                continue
            target = day.replace(hour=self.open_hhmm[0], minute=self.open_hhmm[1], second=0, microsecond=0)
            if target > now:
                return (target - now).total_seconds() / 60.0
        return 24 * 60.0

    @classmethod
    def from_config(cls, raw: dict | str | None) -> "Session":
        if isinstance(raw, str):
            return cls(kind=raw)
        if not raw:
            return cls()
        return cls(
            kind=raw.get("kind", "crypto"),
            tz=raw.get("tz", "America/New_York"),
            open_hhmm=tuple(raw.get("open_hhmm", (9, 30))),
            close_hhmm=tuple(raw.get("close_hhmm", (16, 0))),
        )


# --------------------------------------------------------------------------- #
# anchor
# --------------------------------------------------------------------------- #
@dataclass
class AnchorDecision:
    px: float
    source: str                       # tradfi | rfq | venue_index_median | frozen | none
    frozen: bool = False
    agreement_bps: float = 0.0        # spread between the two venue indices
    reasons: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.px > 0 and self.source != "none"


@dataclass
class AnchorConfig:
    index_agreement_bps: float = 45.0   # venue indices must agree within this
    tradfi_priority: bool = True
    freeze_grace_s: float = 300.0       # keep the last tradfi price this long after close


class AnchorBook:
    """Stateful anchor builder, one instance per symbol."""

    def __init__(self, config: AnchorConfig | None = None, session: Session | None = None):
        self.cfg = config or AnchorConfig()
        self.session = session or Session()
        self._last_tradfi: tuple[float, float] = (0.0, 0.0)   # px, ts
        self._last_rfq: tuple[float, float] = (0.0, 0.0)

    def observe_tradfi(self, px: float, ts: float) -> None:
        if px > 0:
            self._last_tradfi = (px, ts)

    def observe_rfq(self, px: float, ts: float) -> None:
        if px > 0:
            self._last_rfq = (px, ts)

    def decide(self, now: float, okx_index: float, lighter_index: float) -> AnchorDecision:
        reasons: list[str] = []
        agreement = 0.0
        if okx_index > 0 and lighter_index > 0:
            mid = (okx_index + lighter_index) / 2.0
            agreement = abs(okx_index - lighter_index) / mid * 1e4

        tradfi_px, tradfi_ts = self._last_tradfi
        rfq_px, rfq_ts = self._last_rfq
        session_open = self.session.is_open(now)

        # 1) reference market open -> reference price is the anchor
        if self.cfg.tradfi_priority and session_open and tradfi_px > 0 and (now - tradfi_ts) <= self.cfg.freeze_grace_s:
            return AnchorDecision(tradfi_px, "tradfi", False, agreement, ["anchored on tradfi spot (session open)"])

        # 2) RFQ (mintable at the reference price) beats every venue mid
        if rfq_px > 0 and (now - rfq_ts) <= self.cfg.freeze_grace_s:
            return AnchorDecision(rfq_px, "rfq", False, agreement, ["anchored on RFQ mint/burn quote"])

        # 3) two venue indices, but only if they agree
        if okx_index > 0 and lighter_index > 0:
            if agreement > self.cfg.index_agreement_bps:
                return AnchorDecision(0.0, "none", False, agreement, [
                    f"venue indices disagree {agreement:.1f}bps > {self.cfg.index_agreement_bps:.1f}bps — no fair value",
                ])
            median = statistics.fmean([okx_index, lighter_index])
            if not session_open and self.session.kind != "crypto":
                # both venue indices are frozen together while the reference is
                # closed: agreeing with yourself is not price discovery
                return AnchorDecision(median, "frozen", True, agreement, [
                    "reference market closed — median of venue indices is a frozen anchor, reduce-only",
                ])
            return AnchorDecision(median, "venue_index_median", False, agreement,
                                  ["anchored on median of venue indices"])

        # 4) reference closed and only one venue index -> freeze, never extrapolate
        single = okx_index or lighter_index
        if single > 0:
            reasons.append("reference market closed / single index — anchor frozen, reduce-only")
            return AnchorDecision(single, "frozen", True, agreement, reasons)

        return AnchorDecision(0.0, "none", False, agreement, ["no price source available"])

    def staleness_s(self, now: float) -> float:
        return now - max(self._last_tradfi[1], self._last_rfq[1])