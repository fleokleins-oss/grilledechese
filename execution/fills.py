"""
Fills — simulate an order hitting the book.

Produces a FillResult that tells the caller:
  - filled_qty, fill_price  (both floats; fill_price already includes slippage)
  - fee_decimal              (per-side, to be applied by caller via fees module)
  - partial                  (True if filled < requested)
  - delay_ticks              (how many ticks the fill took — 0 in the MVP, but
                              reserved for future latency-aware models)

Slippage is read from execution.slippage — this module is its only caller
for slippage. Fees are read from execution.fees — same discipline.
"""
from __future__ import annotations
from dataclasses import dataclass

from .fees import fee_decimal_for
from .slippage import execution_pressure_z, slippage_decimal


@dataclass
class FillResult:
    filled_qty: float
    fill_price: float
    fee_decimal: float
    partial: bool
    delay_ticks: int
    z_pressure: float
    side: str  # "buy" or "sell"

    def notional(self) -> float:
        return self.filled_qty * self.fill_price


def _desired_price(side: str, features: dict) -> float:
    """Get the top-of-book price relevant for a market order on `side`."""
    ask = float(features.get("best_ask", features.get("mid_price", 0.0)))
    bid = float(features.get("best_bid", features.get("mid_price", 0.0)))
    mid = float(features.get("mid_price", 0.0))
    if side == "buy":
        return ask if ask > 0 else mid
    return bid if bid > 0 else mid


def simulate_fill(
    side: str,
    desired_qty: float,
    desired_notional_usd: float,
    features: dict,
    is_taker: bool = True,
) -> FillResult:
    """
    Simulate hitting the book with a market-like order.

    Args:
      side:                  "buy" or "sell"
      desired_qty:           qty in asset units requested (can be 0 if notional-sized)
      desired_notional_usd:  $ notional requested (used for Z-pressure size term)
      features:              book + microstructure snapshot (see README)
      is_taker:              whether the order pays taker fee

    Returns: FillResult
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if desired_qty < 0 or desired_notional_usd < 0:
        raise ValueError("desired_qty and desired_notional_usd must be non-negative")

    base_price = _desired_price(side, features)
    if base_price <= 0:
        return FillResult(0.0, 0.0, 0.0, False, 0, 0.0, side)

    # Compute Z pressure based on size
    z = execution_pressure_z(features, size_usd=desired_notional_usd)
    slip = slippage_decimal(z, side=side)

    if side == "buy":
        fill_price = base_price * (1.0 + slip)
    else:
        fill_price = base_price * (1.0 - slip)

    # Partial fill check — if depth is thin and size is large, fill what's available
    available_depth_usd = float(features.get("available_depth_usd", desired_notional_usd))
    partial = False
    filled_notional = desired_notional_usd
    if available_depth_usd > 0 and desired_notional_usd > available_depth_usd:
        filled_notional = available_depth_usd
        partial = True

    filled_qty = filled_notional / fill_price if fill_price > 0 else 0.0
    # If caller passed desired_qty directly, honor it as a cap
    if desired_qty > 0:
        filled_qty = min(filled_qty, desired_qty)
        if filled_qty < desired_qty:
            partial = True

    fee_dec = fee_decimal_for(is_taker=is_taker)

    return FillResult(
        filled_qty=float(filled_qty),
        fill_price=float(fill_price),
        fee_decimal=float(fee_dec),
        partial=bool(partial),
        delay_ticks=0,  # MVP: instant fills; reserve slot for latency model
        z_pressure=float(z),
        side=side,
    )
