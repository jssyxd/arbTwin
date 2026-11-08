"""Lighter adapter: mainnet REST + `/stream` websocket, USDC-margined perps.

Verified against the public API on 2026-11-08:

* base url `https://mainnet.zklighter.elliot.ai`, ws `wss://.../stream`
* `GET /api/v1/orderBookDetails` -> per-market static + live fields
  (`mark_price`, `index_price`, `open_interest_usd`, `open_interest_limit`,
  integer `price_decimals` / `size_decimals`, `min_base_amount`,
  `min_quote_amount`, margin fractions in 1e-4 units, `market_deployer`)
* `GET /api/v1/orderBookOrders?market_id=&limit=` -> depth
* WS: subscribe `order_book/{market_id}` and `market_stats/{market_id}`;
  market_stats carries `mark_price`, `index_price`, `premium`,
  `open_interest_usd`, `funding_rate`, `current_funding_rate`,
  `funding_timestamp` (hourly funding)
* prices/sizes are **integers** scaled by the market decimals
* post-only orders have no speed bump and no fee today; taker orders carry a
  speed bump (300 ms on Standard accounts) — that asymmetry is why the Lighter
  leg leads in `arbtwin.spread.build_plan`
* order transaction types: TIF 0=IOC, 1=GTT, 2=post-only; `reduce_only=1`

Trading needs `lighter-sdk` and an API key (`ChangePubKey` registered once from
the L1 address).  Without them the adapter still streams and quotes — it just
refuses to sign.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Optional

from ..config import LighterCredentials
from ..models import Book, Fill, MarketSpec, MarketStats, OrderRequest, VenuePosition
from .base import ThreadedWs, VenueAdapter, VenueError, VenueUnavailable, http_json

PERP = "perp"
DEPTH_LEVELS = 250        # /orderBookOrders max; enough because Lighter books are sparse (~90 levels span 5000bps)


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _LighterWs(ThreadedWs):
    def __init__(self, url: str, market_ids: list[int], sink: "LighterVenue",
                 proxy: Optional[str] = None):
        super().__init__("lighter-ws", url)
        self.market_ids = market_ids
        self.sink = sink
        self.proxy = proxy

    def run_once(self) -> None:  # pragma: no cover - network
        import asyncio

        async def main():
            import websockets

            async with websockets.connect(self.url, ping_interval=20, close_timeout=2,
                                          proxy=self.proxy) as ws:
                for mid in self.market_ids:
                    for ch in (f"order_book/{mid}", f"market_stats/{mid}"):
                        await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=25)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"type": "ping"}))
                        continue
                    self.last_message_at = time.time()
                    self.sink._ingest(json.loads(raw))

        asyncio.run(main())


class LighterVenue(VenueAdapter):
    name = "lighter"
    funding_interval_h = 1.0        # Lighter funds hourly

    def __init__(self, creds: Optional[LighterCredentials] = None, proxy: Optional[str] = None):
        self.creds = creds or LighterCredentials()
        self.proxy = proxy
        self._specs: dict[str, MarketSpec] = {}
        self._market_to_symbol: dict[int, str] = {}
        self._books: dict[str, Book] = {}
        self._stats: dict[str, MarketStats] = {}
        self._lock = threading.Lock()
        self._ws: Optional[_LighterWs] = None
        self.book_ttl_s = 1.5
        self._clients: dict[str, object] = {}
        self._client_error: Optional[str] = None

    # ------------------------------------------------------------------ rest
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        data = http_json(f"{self.creds.base_url}{path}", params=params, proxy=self.proxy)
        code = data.get("code")
        if code not in (200, "200", None):
            raise VenueError(f"lighter {path}: {code} {data.get('message')}")
        return data

    def specs(self, symbols: Optional[list[str]] = None) -> dict[str, MarketSpec]:
        if not self._specs:
            data = self._get("/api/v1/orderBookDetails")
            for item in data.get("order_book_details", []):
                if item.get("market_type") != PERP:
                    continue
                symbol = str(item.get("symbol", "")).upper()
                if not symbol or item.get("status") != "active":
                    continue
                imf = _f(item.get("default_initial_margin_fraction"), 500) / 10_000.0
                mmf = _f(item.get("maintenance_margin_fraction"), 120) / 10_000.0
                spec = MarketSpec(
                    venue=self.name,
                    symbol=symbol,
                    market_ref=str(item.get("market_id")),
                    price_decimals=int(item.get("price_decimals") or item.get("supported_price_decimals") or 2),
                    size_decimals=int(item.get("size_decimals") or item.get("supported_size_decimals") or 4),
                    min_base=_f(item.get("min_base_amount")),
                    min_quote=_f(item.get("min_quote_amount")),
                    contract_multiplier=1.0,      # Lighter sizes are base units
                    initial_margin_frac=imf or 0.05,
                    maintenance_margin_frac=mmf or 0.012,
                    maker_fee_bps=_f(item.get("maker_fee")) * 1e4 if item.get("is_maker_fee_enabled") else 0.0,
                    taker_fee_bps=_f(item.get("taker_fee")) * 1e4 if item.get("is_taker_fee_enabled") else 0.0,
                    oi_limit_usd=_f(item.get("open_interest_limit")) * _f(item.get("mark_price") or item.get("last_trade_price")),
                    max_leverage=(1.0 / imf) if imf else 20.0,
                    status="active",
                )
                self._specs[symbol] = spec
                self._market_to_symbol[int(spec.market_ref)] = symbol
                self._stats[symbol] = MarketStats(
                    venue=self.name, symbol=symbol,
                    mark_price=_f(item.get("mark_price")), index_price=_f(item.get("index_price")),
                    last_price=_f(item.get("last_trade_price")),
                    oi_usd=_f(item.get("open_interest_usd")),
                    oi_limit_usd=spec.oi_limit_usd,
                    funding_interval_h=self.funding_interval_h,
                )
        if symbols:
            return {s: self._specs[s] for s in symbols if s in self._specs}
        return dict(self._specs)

    def book(self, symbol: str) -> Book:
        spec = self._specs.get(symbol)
        if not spec:
            raise VenueError(f"lighter: unknown symbol {symbol}")
        with self._lock:
            cached = self._books.get(symbol)
        if cached and cached.valid(3.0):
            return cached
        data = http_json(f"{self.creds.base_url}/api/v1/orderBookOrders",
                         params={"market_id": spec.market_ref, "limit": DEPTH_LEVELS},
                         proxy=self.proxy, timeout=6.0, retries=1)
        if data.get("code") not in (200, "200", None):
            raise VenueError(f"lighter depth: {data.get('code')} {data.get('message')}")
        book = _parse_book(self.name, symbol, data)
        with self._lock:
            self._books[symbol] = book
        return book

    def stats(self, symbol: str) -> MarketStats:
        with self._lock:
            cached = self._stats.get(symbol)
        if cached and cached.ts and (time.time() - cached.ts) < 10:
            return cached
        spec = self._specs.get(symbol) or self.specs([symbol]).get(symbol)
        if not spec:
            raise VenueError(f"lighter: unknown symbol {symbol}")
        data = self._get("/api/v1/orderBookDetails", {"market_id": spec.market_ref})
        details = data.get("order_book_details") or []
        item = details[0] if details else {}
        stats = MarketStats(
            venue=self.name, symbol=symbol,
            mark_price=_f(item.get("mark_price")),
            index_price=_f(item.get("index_price")),
            last_price=_f(item.get("last_trade_price")),
            oi_usd=_f(item.get("open_interest_usd")),
            oi_limit_usd=spec.oi_limit_usd,
            funding_interval_h=self.funding_interval_h,
            ts=time.time(),
        )
        # funding lives on the WS market_stats channel; REST fundings endpoint is history
        with self._lock:
            prev = self._stats.get(symbol)
            if prev and prev.funding_rate:
                stats.funding_rate = prev.funding_rate
            self._stats[symbol] = stats
        return stats

    def _ingest(self, msg: dict) -> None:
        channel = str(msg.get("channel", ""))
        if channel.startswith("order_book:"):
            mid = int(channel.split(":")[1])
            symbol = self._market_to_symbol.get(mid)
            if not symbol:
                return
            ob = msg.get("order_book") or {}
            with self._lock:
                self._books[symbol] = Book(
                    self.name, symbol,
                    bids=[(_f(x["price"]), _f(x["size"])) for x in ob.get("bids", [])],
                    asks=[(_f(x["price"]), _f(x["size"])) for x in ob.get("asks", [])],
                )
        elif channel.startswith("market_stats:"):
            mid = int(channel.split(":")[1])
            symbol = self._market_to_symbol.get(mid)
            ms = msg.get("market_stats") or {}
            if not symbol or not ms:
                return
            with self._lock:
                prev = self._stats.get(symbol)
                self._stats[symbol] = MarketStats(
                    venue=self.name, symbol=symbol,
                    mark_price=_f(ms.get("mark_price")),
                    index_price=_f(ms.get("index_price")),
                    last_price=_f(ms.get("last_trade_price")),
                    mid_price=_f(ms.get("mid_price")),
                    oi_usd=_f(ms.get("open_interest_usd")),
                    oi_limit_usd=prev.oi_limit_usd if prev else 0.0,
                    # Lighter publishes PERCENT per hour; store fractions per interval
                    funding_rate=(_f(ms.get("current_funding_rate") or ms.get("funding_rate"))) / 100.0,
                    funding_interval_h=self.funding_interval_h,
                    premium=_f(ms.get("premium")),
                    ts=time.time(),
                )

    def start(self, symbols: list[str]) -> None:
        self.specs(symbols)
        market_ids = [int(s.market_ref) for s in self.specs(symbols).values()]
        if not market_ids:
            return
        self._ws = _LighterWs(self.creds.ws_url, market_ids, self, proxy=self.proxy)
        self._ws.start()

    def stop(self) -> None:
        if self._ws:
            self._ws.stop()

    def ws_health(self) -> dict:
        if not self._ws:
            return {"connected": False, "age_s": None}
        age = time.time() - self._ws.last_message_at if self._ws.last_message_at else None
        return {"connected": age is not None and age < 30, "age_s": age,
                "reconnects": self._ws.reconnects, "error": self._ws.last_error}

    # ------------------------------------------------------------------ account
    def _trader(self, taker: bool = False):
        """Quoting and taking use different API keys (Lighter serialises per key)."""
        slot = "taker" if taker and self.creds.has_taker_key else "maker"
        if slot in self._clients:
            return self._clients[slot]
        if self._client_error is not None:
            raise VenueUnavailable(self._client_error)
        if not self.creds.has_private:
            self._client_error = ("lighter trading needs LIGHTER_API_PRIVATE_KEY / "
                                  "LIGHTER_API_KEY_INDEX / LIGHTER_ACCOUNT_INDEX")
            raise VenueUnavailable(self._client_error)
        from .lighter_trade import LighterTradeClient

        if slot == "taker":
            key, index = self.creds.api_private_key_taker, self.creds.api_key_index_taker
        else:
            key, index = self.creds.api_private_key, self.creds.api_key_index
        self._clients[slot] = LighterTradeClient(
            base_url=self.creds.base_url,
            account_index=self.creds.account_index,
            api_private_key=key,
            api_key_index=index,
        )
        return self._clients[slot]

    def positions(self) -> dict[str, VenuePosition]:
        if self.creds.account_index < 0:
            return {}
        data = self._get("/api/v1/account", {"by": "index", "value": self.creds.account_index})
        accounts = data.get("accounts") or []
        if not accounts:
            return {}
        out: dict[str, VenuePosition] = {}
        for pos in accounts[0].get("positions", []):
            symbol = str(pos.get("symbol", "")).upper()
            # `sign`: 1 = long; anything else = short (0 or -1 depending on API version)
            direction = 1 if int(_f(pos.get("sign"), 0)) == 1 else -1
            qty = abs(_f(pos.get("position"))) * direction
            if not symbol or abs(qty) < 1e-12:
                continue
            out[symbol] = VenuePosition(
                venue=self.name, symbol=symbol, qty_base=qty,
                avg_entry=_f(pos.get("avg_entry_price")),
                liquidation_price=_f(pos.get("liquidation_price")),
                notional_usd=abs(_f(pos.get("position_value"))),
                margin_usd=_f(pos.get("allocated_margin")),
                unrealized_pnl=_f(pos.get("unrealized_pnl")),
            )
        return out

    def equity_usd(self) -> float:
        if self.creds.account_index < 0:
            return 0.0
        data = self._get("/api/v1/account", {"by": "index", "value": self.creds.account_index})
        accounts = data.get("accounts") or []
        if not accounts:
            return 0.0
        acct = accounts[0]
        # `assets[].balance` can be "0.000000" for a funded account (measured:
        # balance 0, margin_balance 27.63, collateral 27.63).  Prefer the
        # account-level totals, fall back to the asset row.
        for field in ("total_asset_value", "collateral", "available_balance"):
            value = _f(acct.get(field))
            if value > 0:
                return value
        for asset in acct.get("assets", []):
            if asset.get("symbol") == "USDC":
                return max(_f(asset.get("margin_balance")), _f(asset.get("balance")))
        return 0.0

    # ------------------------------------------------------------------ trading
    def place(self, order: OrderRequest, spec: MarketSpec) -> Fill:
        """Sign + send one order.  Prices and sizes go over the wire as integers."""
        trader = self._trader(taker=(order.tif == "ioc"))
        price_int = int(round(order.px * 10 ** spec.price_decimals))
        size_int = int(round(order.qty_base * 10 ** spec.size_decimals))
        if size_int <= 0:
            raise VenueError("lighter: order rounds to zero size")
        # Lighter dedupes on client_order_index, so two orders must never share one.
        # A wall-clock modulo is not monotonic (same millisecond -> same index, and
        # two processes collide).  Seed from the clock once, then count.
        client_order_index = next(self._order_seq) % (2 ** 48 - 1)
        resp = trader.create_order(
            market_index=int(spec.market_ref),
            client_order_index=client_order_index,
            base_amount=size_int,
            price=price_int,
            is_ask=1 if order.side == "sell" else 0,
            tif=order.tif,
            reduce_only=order.reduce_only,
            order_expiry=0 if order.tif == "ioc" else int(time.time() * 1000) + 5 * 60 * 1000,
        )
        fill_ts = time.time()
        if order.tif == "post_only":
            # a post-only order rests: report it, the fill arrives on the book
            with self._lock:
                book = self._books.get(order.symbol)
            if book and book.ts:
                fill_ts = book.ts
        return Fill(self.name, order.symbol, order.side, size_int / 10 ** spec.size_decimals,
                    price_int / 10 ** spec.price_decimals,
                    fee_bps=spec.maker_fee_bps if order.tif == "post_only" else spec.taker_fee_bps,
                    ts=fill_ts, order_id=str(client_order_index), raw=resp)

    def cancel(self, symbol: str, order_id: str) -> bool:
        spec = self._specs.get(symbol)
        if not spec:
            return False
        try:
            self._trader().cancel_order(int(spec.market_ref), int(order_id))
            return True
        except Exception:
            return False

    def cancel_all(self, symbol: str) -> int:
        spec = self._specs.get(symbol)
        if not spec or not self.creds.has_private:
            return 0
        try:
            self._trader().cancel_all(int(spec.market_ref))
            return 1
        except Exception:
            return 0


def _parse_book(venue: str, symbol: str, payload: dict) -> Book:
    """Accepts both `/orderBookOrders` and a WS `order_book` payload.

    REST order rows carry `remaining_base_amount`; WS rows carry `size`.
    """
    node = payload.get("order_book") or payload
    def size_of(x: dict) -> float:
        for key in ("size", "remaining_base_amount", "base_amount"):
            if key in x:
                return _f(x[key])
        return 0.0
    bids = [(_f(x["price"]), size_of(x)) for x in (node.get("bids") or [])]
    asks = [(_f(x["price"]), size_of(x)) for x in (node.get("asks") or [])]
    return Book(venue, symbol, bids=bids, asks=asks)