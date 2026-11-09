"""Fill model interface with conservative default and queue-reactive hook.

The fill model answers: "does this resting order get filled against this
incoming trade or book update?"  It consumes a trade/book update, the resting
order's side/px/qty, and (for queue-aware models) the queue-ahead volume.

Default (conservative): price-through only — the order fills only when the trade
price *crosses* the resting price, i.e. aggressive liquidity removes our level.
This is the most pessimistic fill assumption and gives a lower bound on fill
probability.

A queue-reactive model (Maglaras–Moallemi–Wang style) can be plugged in via
`set_fill_model()` and may fill at the same price when the trade matches at our
level and we are near the front of the queue.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol


class FillModel(Protocol):
    """Callable: (trade_or_book, resting_order, queue_ahead) -> fill_qty.

    `trade_or_book` can be:
      - dict with keys px, sz, side, ts (a trade)
      - dict with keys bids, asks (a book snapshot; best bid/ask used)

    `resting_order`: dict with keys side ("buy"|"sell"), px, remaining

    `queue_ahead`: float or None — estimated notional ahead in the queue at our
    level.  None or 0 means "at the front" (conservative default ignores this).
    """

    def __call__(
        self,
        trade_or_book: dict,
        resting_order: dict,
        queue_ahead: Optional[float] = None,
    ) -> float: ...


# ── Conservative (price-through only) ──────────────────────────────────────


def conservative_fill(
    trade_or_book: dict,
    resting_order: dict,
    queue_ahead: Optional[float] = None,
) -> float:
    """Fill ONLY when the trade price crosses the resting price.

    - Resting buy at px: fills on any trade <= px (someone sold at or below our bid)
    - Resting sell at px: fills on any trade >= px (someone bought at or above our ask)

    For book updates: the best counter-side price crossing counts as a fill.
    """
    side = resting_order.get("side", "buy")
    our_px = resting_order.get("px", 0.0)
    remaining = resting_order.get("remaining", 0.0)
    if remaining <= 0 or our_px <= 0:
        return 0.0

    # Trade input
    trade_px = trade_or_book.get("px")
    if trade_px is not None:
        if side == "buy" and trade_px <= our_px:
            return remaining
        if side == "sell" and trade_px >= our_px:
            return remaining
        return 0.0

    # Book input: check if the counter side crossed
    bids = trade_or_book.get("bids")
    asks = trade_or_book.get("asks")
    if bids and side == "sell":
        best_bid = bids[0] if isinstance(bids[0], (int, float)) else bids[0][0]
        if best_bid >= our_px:
            return remaining
    if asks and side == "buy":
        best_ask = asks[0] if isinstance(asks[0], (int, float)) else asks[0][0]
        if best_ask <= our_px:
            return remaining
    return 0.0


# ── Queue-reactive model ───────────────────────────────────────────────────


def queue_reactive_fill(
    trade_or_book: dict,
    resting_order: dict,
    queue_ahead: Optional[float] = None,
) -> float:
    """Fill at our level when the trade matches and queue_ahead has been cleared.

    This is a simplified Maglaras–Moallemi–Wang model:
    - If price crosses: full fill (aggressive taker).
    - If price matches exactly and queue_ahead <= 0: full fill (our turn at the front).
    - If price matches and queue_ahead > 0: partial fill proportional to
      trade size / (queue_ahead + resting remaining), clipped to remaining.
    """
    side = resting_order.get("side", "buy")
    our_px = resting_order.get("px", 0.0)
    remaining = resting_order.get("remaining", 0.0)
    if remaining <= 0 or our_px <= 0:
        return 0.0

    trade_px = trade_or_book.get("px")
    trade_sz = trade_or_book.get("sz", 0.0)
    if trade_px is None:
        # Book update: only cross-check (no trade to measure queue consumption)
        bids = trade_or_book.get("bids")
        asks = trade_or_book.get("asks")
        if bids and side == "sell":
            best_bid = bids[0] if isinstance(bids[0], (int, float)) else bids[0][0]
            if best_bid >= our_px:
                return remaining
        if asks and side == "buy":
            best_ask = asks[0] if isinstance(asks[0], (int, float)) else asks[0][0]
            if best_ask <= our_px:
                return remaining
        return 0.0

    # Price-through: full fill
    if side == "buy" and trade_px < our_px:
        return remaining
    if side == "sell" and trade_px > our_px:
        return remaining

    # Price match at our level
    if abs(trade_px - our_px) < 1e-12:
        qa = queue_ahead or 0.0
        if qa <= 0:
            return min(trade_sz, remaining)
        # Proportional fill
        proportion = trade_sz / max(qa + remaining, 1e-12)
        return min(proportion * remaining, remaining)

    return 0.0


# ── Global model hook ──────────────────────────────────────────────────────

_default_model: FillModel = conservative_fill


def get_fill_model() -> FillModel:
    return _default_model


def set_fill_model(model: FillModel) -> None:
    global _default_model
    _default_model = model