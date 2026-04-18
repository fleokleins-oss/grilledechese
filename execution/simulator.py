"""
Simulator step — applies a decision to a creature for one tick.

This is the ONLY place that mutates the creature's capital/position through
execution. World3D calls step_execution() per tick per creature.
"""
from __future__ import annotations

from .fills import simulate_fill, FillResult
from .fees import should_charge_funding, funding_charge_decimal


def step_execution(
    creature,
    tick: int,
    action: str,
    size_fraction: float,
    features: dict,
    is_taker: bool = True,
) -> dict:
    """
    Apply a decision to a creature for this tick.

    Returns a dict describing what happened:
      {
        "action": <echo>,
        "filled": bool,
        "fill_price": float or None,
        "filled_qty": float,
        "z_pressure": float,     # Z at the fill (or computed for the check)
        "funding_charged": bool,
      }

    The creature's capital/position is mutated via creature.apply_buy/sell.
    """
    info = {
        "action": action,
        "filled": False,
        "fill_price": None,
        "filled_qty": 0.0,
        "z_pressure": 0.0,
        "funding_charged": False,
    }

    mid = float(features.get("mid_price", 0.0))

    # -------- Funding (once per interval, regardless of action) ----------
    if should_charge_funding(tick) and creature.position > 0 and mid > 0:
        pos_notional = creature.position * mid
        charge = funding_charge_decimal(pos_notional)
        creature.capital -= charge
        info["funding_charged"] = True

    # -------- Action dispatch --------------------------------------------
    if action == "HOLD":
        return info

    if action == "BUY":
        if creature.position > 0:
            return info  # defensive — actions.decide prevents this
        # Size the order in USD
        notional = float(creature.capital) * max(0.0, float(size_fraction))
        if notional <= 1e-6 or mid <= 0:
            return info
        fill: FillResult = simulate_fill(
            side="buy",
            desired_qty=0.0,
            desired_notional_usd=notional,
            features=features,
            is_taker=is_taker,
        )
        info["z_pressure"] = fill.z_pressure
        if fill.filled_qty <= 0:
            return info
        creature.apply_buy(
            tick=tick,
            fill_price=fill.fill_price,
            qty=fill.filled_qty,
            fee_decimal=fill.fee_decimal,
        )
        info.update({
            "filled": True,
            "fill_price": fill.fill_price,
            "filled_qty": fill.filled_qty,
        })
        return info

    if action in ("SELL_FULL", "SELL_PARTIAL"):
        if creature.position <= 0:
            return info
        qty = creature.position if action == "SELL_FULL" else creature.position * max(
            0.0, min(1.0, float(size_fraction))
        )
        notional = qty * mid
        fill = simulate_fill(
            side="sell",
            desired_qty=qty,
            desired_notional_usd=notional,
            features=features,
            is_taker=is_taker,
        )
        info["z_pressure"] = fill.z_pressure
        if fill.filled_qty <= 0:
            return info
        creature.apply_sell(
            tick=tick,
            fill_price=fill.fill_price,
            qty=fill.filled_qty,
            fee_decimal=fill.fee_decimal,
        )
        info.update({
            "filled": True,
            "fill_price": fill.fill_price,
            "filled_qty": fill.filled_qty,
        })
        return info

    return info
