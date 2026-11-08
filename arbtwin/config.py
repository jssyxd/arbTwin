"""Layered configuration loading: YAML files + environment overrides.

Nothing here ever logs a secret.  `Config.redacted()` is what the CLI prints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class OkxCredentials:
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    simulated: bool = False
    base_url: str = "https://www.okx.com"
    ws_public: str = "wss://ws.okx.com:8443/ws/v5/public"
    ws_private: str = "wss://ws.okx.com:8443/ws/v5/private"

    @property
    def has_private(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)

    @classmethod
    def from_env(cls) -> "OkxCredentials":
        return cls(
            api_key=_env("OKX_API_KEY"),
            api_secret=_env("OKX_API_SECRET"),
            passphrase=_env("OKX_API_PASSPHRASE"),
            simulated=_env_bool("OKX_SIMULATED", False),
            base_url=_env("OKX_BASE_URL", "https://www.okx.com").rstrip("/"),
            ws_public=_env("OKX_WS_PUBLIC", "wss://ws.okx.com:8443/ws/v5/public"),
            ws_private=_env("OKX_WS_PRIVATE", "wss://ws.okx.com:8443/ws/v5/private"),
        )


@dataclass
class LighterCredentials:
    api_private_key: str = ""
    api_key_index: int = -1
    account_index: int = -1
    base_url: str = "https://mainnet.zklighter.elliot.ai"
    ws_url: str = "wss://mainnet.zklighter.elliot.ai/stream"

    @property
    def has_private(self) -> bool:
        return bool(self.api_private_key) and self.api_key_index >= 0 and self.account_index >= 0

    @classmethod
    def from_env(cls) -> "LighterCredentials":
        return cls(
            api_private_key=_env("LIGHTER_API_PRIVATE_KEY"),
            api_key_index=_env_int("LIGHTER_API_KEY_INDEX", -1),
            account_index=_env_int("LIGHTER_ACCOUNT_INDEX", -1),
            base_url=_env("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai").rstrip("/"),
            ws_url=_env("LIGHTER_WS_URL", "wss://mainnet.zklighter.elliot.ai/stream"),
        )


@dataclass
class EngineLimits:
    """Hard risk limits.  Every one of them can veto a trade on its own."""

    max_margin_usage_pct: float = 0.35       # per venue, of that venue's own equity
    max_notional_usd: float = 3000.0         # per symbol across both legs
    max_open_symbols: int = 4
    max_unhedged_notional_usd: float = 150.0
    max_unhedged_ms: int = 800
    max_funding_carry_apr_pct: float = 25.0   # net funding cost you are willing to pay
    min_liq_distance_pct: float = 0.18
    max_oi_utilization: float = 0.25
    max_mark_divergence_bps: float = 40.0
    max_quote_ttl_s: int = 120
    max_book_age_s: float = 3.0
    expected_hold_h: float = 2.0
    max_hold_h: float = 4.0            # basis reverts in <2h (measured half-life 0.8-1.7h)
    cooldown_after_fill_s: float = 10.0
    emergency_stop_loss_pct: float = 0.12    # single-leg adverse move from entry
    min_depth_usd_1pct: float = 2000.0
    max_slippage_bps: float = 6.0


@dataclass
class BandParams:
    k_sigma: float = 2.0
    floor_bps: float = 8.0
    cap_bps: float = 600.0
    fee_multiple: float = 1.0
    degenerate_sigma_bps: float = 3.0
    degenerate_fee_multiple: float = 1.5
    exit_ratio: float = 0.55


@dataclass
class AppConfig:
    okx: OkxCredentials = field(default_factory=OkxCredentials)
    lighter: LighterCredentials = field(default_factory=LighterCredentials)
    limits: EngineLimits = field(default_factory=EngineLimits)
    band: BandParams = field(default_factory=BandParams)
    venues: dict = field(default_factory=dict)
    symbols: list[dict] = field(default_factory=list)
    risk: dict = field(default_factory=dict)
    state_dir: str = "state"
    # "maker_basis" = the original two-mode engine; "wave" = one-round-at-a-time
    # state machine (arbtwin/wave.py) that never takes liquidity except on a stop.
    strategy: str = "maker_basis"
    dry_run: bool = True
    alert_webhook: str = ""
    poll_interval_ms: int = 250

    @classmethod
    def load(cls, venues_path: Optional[str] = None, risk_path: Optional[str] = None,
             symbols_path: Optional[str] = None) -> "AppConfig":
        venues = _load_yaml(venues_path or _env("ARBTWIN_CONFIG", "config/venues.yaml"))
        risk = _load_yaml(risk_path or _env("ARBTWIN_RISK", "config/risk.yaml"))
        symbols = _load_yaml(symbols_path or _env("ARBTWIN_SYMBOLS", "config/symbols.yaml"))

        limits = EngineLimits()
        for key, value in (risk.get("limits") or {}).items():
            if hasattr(limits, key):
                setattr(limits, key, value)
        band = BandParams()
        for key, value in (risk.get("bands") or {}).items():
            if hasattr(band, key):
                setattr(band, key, value)

        cfg = cls(
            strategy=str(risk.get("strategy") or "maker_basis"),
            okx=OkxCredentials.from_env(),
            lighter=LighterCredentials.from_env(),
            limits=limits,
            band=band,
            venues=venues,
            symbols=list(symbols.get("approved") or []),
            risk=risk,
            state_dir=_env("ARBTWIN_STATE_DIR", "state"),
            dry_run=_env_bool("ARBTWIN_DRY_RUN", True),
            alert_webhook=_env("ARBTWIN_ALERT_WEBHOOK"),
            poll_interval_ms=_env_int("ARBTWIN_POLL_MS", 250),
        )
        return cfg

    def venue_mechanics(self, venue: str) -> dict:
        return ((self.venues.get("venues") or {}).get(venue) or {})

    def redacted(self) -> dict[str, Any]:
        def mask(value: str) -> str:
            if not value:
                return "<unset>"
            return value[:4] + "***" if len(value) > 6 else "<set>"

        return {
            "okx": {
                "base_url": self.okx.base_url,
                "api_key": mask(self.okx.api_key),
                "simulated": self.okx.simulated,
                "ws_public": self.okx.ws_public,
            },
            "lighter": {
                "base_url": self.lighter.base_url,
                "api_key_index": self.lighter.api_key_index,
                "account_index": self.lighter.account_index,
                "private_key": mask(self.lighter.api_private_key),
            },
            "dry_run": self.dry_run,
            "symbols": len(self.symbols),
            "state_dir": self.state_dir,
        }


def write_state(path: str | Path, payload: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix == ".json":
        import json

        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    else:
        with p.open("a", encoding="utf-8") as fh:
            import json

            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return p