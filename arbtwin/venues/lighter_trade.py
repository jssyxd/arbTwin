"""Live trading bridge for Lighter (lazy, optional dependency).

`lighter-sdk` (v1.1.8+) exposes an **async** `SignerClient`: it signs a
transaction, sends it, and returns `(tx, response, error)`.  The engine is
synchronous (a poll loop), so this module owns a private event loop in a
background thread and marshals calls onto it.

Facts encoded here, read from `lighter/signer_client.py` @ v1.1.8:

* `SignerClient(url=..., account_index=..., api_private_keys={index: pk})`
* `ORDER_TYPE_LIMIT = 0`, `ORDER_TYPE_MARKET = 1`
* `ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL = 0`, `..._GOOD_TILL_TIME = 1`,
  `..._POST_ONLY = 2`
* `await client.create_order(market_index, client_order_index, base_amount,
  price, is_ask, order_type, time_in_force, reduce_only, trigger_price,
  order_expiry, ...) -> (tx, RespSendTx, error)`
* `await client.cancel_order(market_index, order_index)`
* `await client.cancel_all_orders(time_in_force, timestamp_ms, market_index)`
* `await client.close()`

Nothing in this file is imported unless live trading is enabled.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional


class AsyncBridge:
    """A private event loop so sync callers can drive an async SDK."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="lighter-async", daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(5.0)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def call(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=30)

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


class LighterTradeClient:
    """Minimal, explicit wrapper: create / cancel / cancel-all / close."""

    TIF = {"ioc": "ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
           "gtc": "ORDER_TIME_IN_FORCE_GOOD_TILL_TIME",
           "post_only": "ORDER_TIME_IN_FORCE_POST_ONLY"}

    def __init__(self, base_url: str, account_index: int, api_private_key: str,
                 api_key_index: int):
        self.base_url = base_url
        self.account_index = account_index
        self.api_private_key = api_private_key
        self.api_key_index = api_key_index
        self._bridge: Optional[AsyncBridge] = None
        self._client = None
        self._error: Optional[str] = None

    # ------------------------------------------------------------------ setup
    def _ensure(self):
        if self._client is not None:
            return self._client
        if self._error:
            raise RuntimeError(self._error)
        try:
            import lighter  # type: ignore
        except ImportError:
            self._error = "pip install 'lighter-sdk>=1.1.8' (extras: live)"
            raise RuntimeError(self._error)
        self._bridge = AsyncBridge()

        async def _make_client():
            # SignerClient builds an aiohttp connector, which needs a running loop.
            return lighter.SignerClient(
                url=self.base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: self.api_private_key},
            )

        self._client = self._bridge.call(_make_client())
        # surfaces a misconfigured key/margin setup before the first order
        self.check()
        return self._client

    def _const(self, name: str) -> int:
        client = self._ensure()
        aliases = {
            "ORDER_TYPE_LIMIT": ("ORDER_TYPE_LIMIT",),
            "POST_ONLY": ("ORDER_TIME_IN_FORCE_POST_ONLY", "ORDER_TIF_POST_ONLY"),
            "IOC": ("ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", "ORDER_TIF_IOC"),
            "GTC": ("ORDER_TIME_IN_FORCE_GOOD_TILL_TIME", "ORDER_TIF_GTT"),
        }[name]
        for attr in aliases:
            if hasattr(client, attr):
                return getattr(client, attr)
        raise RuntimeError(f"lighter-sdk is missing {name}; version too old")

    # ------------------------------------------------------------------ calls
    def check(self) -> None:
        client = self._client
        if client is None:
            client = self._ensure()
        fn = getattr(client, "check_client", None)
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:  # pragma: no cover - live only
            raise RuntimeError(f"lighter client check failed: {exc}") from exc

    def create_order(self, market_index: int, client_order_index: int, base_amount: int,
                     price: int, is_ask: int, tif: str, reduce_only: bool = False,
                     order_expiry: int = 0) -> dict:
        client = self._ensure()
        tif_const = self._const({"ioc": "IOC", "gtc": "GTC"}.get(tif, "POST_ONLY"))
        coro = client.create_order(
            market_index=market_index,
            client_order_index=client_order_index,
            base_amount=base_amount,
            price=price,
            is_ask=is_ask,
            order_type=self._const("ORDER_TYPE_LIMIT"),
            time_in_force=tif_const,
            reduce_only=reduce_only,
            trigger_price=0,
            order_expiry=order_expiry,
            api_key_index=self.api_key_index,
        )
        tx, resp, err = self._bridge.call(coro)
        if err:
            raise RuntimeError(f"lighter create_order: {err}")
        return {"client_order_index": client_order_index, "tx": str(tx)[:120],
                "resp": getattr(resp, "to_dict", lambda: resp)() if resp is not None else None}

    def cancel_order(self, market_index: int, order_index: int) -> dict:
        client = self._ensure()
        coro = client.cancel_order(market_index=market_index, order_index=order_index,
                                   api_key_index=self.api_key_index)
        tx, resp, err = self._bridge.call(coro)
        if err:
            raise RuntimeError(f"lighter cancel_order: {err}")
        return {"order_index": order_index}

    def cancel_all(self, market_index: int) -> dict:
        client = self._ensure()
        tif = 0 if hasattr(client, "CANCEL_ALL_TIF_IMMEDIATE") else 1
        coro = client.cancel_all_orders(time_in_force=tif, timestamp_ms=0,
                                        cancel_all_market_index=market_index,
                                        api_key_index=self.api_key_index)
        tx, resp, err = self._bridge.call(coro)
        if err:
            raise RuntimeError(f"lighter cancel_all: {err}")
        return {"market_index": market_index}

    def close(self) -> None:
        if self._client is not None and self._bridge is not None:
            try:
                self._bridge.call(self._client.close())
            except Exception:
                pass
        if self._bridge is not None:
            self._bridge.shutdown()


class DryTradeClient:
    """Deterministic stand-in used by dry-run and by tests."""

    def __init__(self, on_order: Optional[Callable[[dict], None]] = None):
        self.on_order = on_order
        self.orders: list[dict] = []

    def create_order(self, **kwargs) -> dict:
        self.orders.append(kwargs)
        if self.on_order:
            self.on_order(kwargs)
        return {"dry": True, **{k: v for k, v in kwargs.items() if k in ("client_order_index", "price", "base_amount")}}

    def cancel_order(self, market_index: int, order_index: int) -> dict:
        return {"dry": True, "cancelled": order_index}

    def cancel_all(self, market_index: int) -> dict:
        return {"dry": True, "cancelled_market": market_index}

    def close(self) -> None:
        return None