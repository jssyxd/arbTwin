"""Shared venue plumbing: HTTP, errors, the adapter contract.

Only stdlib here.  A trading adapter that needs a 40 MB dependency tree to
fetch a JSON book is a trading adapter you cannot run on a $5 VPS at 3 a.m.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..models import Book, Fill, MarketSpec, MarketStats, OrderRequest, VenuePosition


class VenueError(RuntimeError):
    """The venue answered, and the answer was no."""


class VenueUnavailable(VenueError):
    """The venue could not be reached, or a required capability is missing."""


DEFAULT_UA = "arbtwin/0.1 (+https://github.com/jssyxd/arbTwin)"


def _prefer_ipv4() -> None:
    """Resolve IPv4 first.

    Venue API allowlists are IPv4 in practice (OKX: 155.254.60.38).  A dual-stack
    host otherwise egresses over IPv6 and every private call fails with 50110
    "Your IP 2400:... is not included in your API key's IP whitelist" -- observed
    on the trading VPS.  IPv6-only hosts still work because we only reorder.
    """
    if os.environ.get("ARBTWIN_FORCE_IPV4", "1") != "1":
        return
    import socket as _socket

    if getattr(_socket.getaddrinfo, "_arbtwin_ipv4", False):
        return
    original = _socket.getaddrinfo

    def prefer(host, port, family=0, type=0, proto=0, flags=0):
        results = original(host, port, family, type, proto, flags)
        v4 = [r for r in results if r[0] == _socket.AF_INET]
        return v4 or results

    prefer._arbtwin_ipv4 = True  # type: ignore[attr-defined]
    _socket.getaddrinfo = prefer  # type: ignore[assignment]


_prefer_ipv4()


def http_json(
    url: str,
    method: str = "GET",
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 20.0,
    retries: int = 3,
    proxy: Optional[str] = None,
) -> Any:
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    payload = json.dumps(body).encode() if body is not None else None
    # Reads may be retried; signed writes (POST) must never be replayed blindly:
    # a timeout can mean the order landed, and a retry would double it.
    attempts = 1 if method.upper() not in ("GET", "HEAD") else retries + 1

    last_err: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=payload, method=method.upper())
        req.add_header("User-Agent", DEFAULT_UA)
        req.add_header("Accept", "application/json")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)

        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            last_err = VenueError(f"HTTP {exc.code} {url}: {detail}")
            if exc.code in (401, 403, 404, 400):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = VenueUnavailable(f"{url}: {exc}")
        time.sleep(min(2.0, 0.5 * (2 ** attempt)))
    raise last_err or VenueUnavailable(url)


class VenueAdapter(ABC):
    """The whole surface the engine is allowed to touch."""

    name: str = "?"
    funding_interval_h: float = 8.0

    @abstractmethod
    def specs(self, symbols: Optional[list[str]] = None) -> dict[str, MarketSpec]: ...

    @abstractmethod
    def book(self, symbol: str) -> Book: ...

    @abstractmethod
    def stats(self, symbol: str) -> MarketStats: ...

    @abstractmethod
    def positions(self) -> dict[str, VenuePosition]: ...

    @abstractmethod
    def equity_usd(self) -> float: ...

    @abstractmethod
    def place(self, order: OrderRequest, spec: MarketSpec) -> Fill: ...

    @abstractmethod
    def cancel_all(self, symbol: str) -> int: ...

    def start(self, symbols: list[str]) -> None:
        """Optional streaming start; REST fallback is always available."""

    def stop(self) -> None:
        pass

    # ---- helpers shared by adapters ----
    def _stale(self, ts: float, max_age_s: float) -> bool:
        return (time.time() - ts) > max_age_s


class ThreadedWs(threading.Thread):
    """Reconnect-forever websocket thread; subclasses implement `run_once`."""

    def __init__(self, name: str, url: str):
        super().__init__(name=name, daemon=True)
        self.url = url
        self._stop = threading.Event()
        self.last_message_at = 0.0
        self.reconnects = 0
        self.last_error = ""

    def run(self) -> None:  # pragma: no cover - network
        # `run_once` owns its event loop (each adapter calls asyncio.run itself);
        # wrapping it here as well would nest two loops and kill the stream.
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.last_error = str(exc)[:200]
                self.reconnects += 1
                time.sleep(min(30.0, 1.0 * self.reconnects))
            if self._stop.is_set():
                return
            time.sleep(0.5)

    def run_once(self) -> None:  # pragma: no cover - network
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()