"""
Fees — SINGLE source of truth.

No other module in encruzilhada3d is allowed to subtract fees. If you find
a `pnl -= something_that_looks_like_a_fee` anywhere else, it is a bug.

All fees are expressed in decimal form. `apply_fee_decimal(notional, fee_dec)`
returns the fee in quote-currency units.

Funding is handled here too, charged once per funding interval by the
simulator — not per tick, not per trade.
"""
from __future__ import annotations
import os

# -----------------------------------------------------------------------------
# Rates (in bps = 1e-4; converted to decimal once and reused)
# -----------------------------------------------------------------------------
FEE_BPS_TAKER = float(os.getenv("ENC3D_FEE_BPS_TAKER", "5.0"))    # 5 bps taker default
FEE_BPS_MAKER = float(os.getenv("ENC3D_FEE_BPS_MAKER", "1.0"))    # 1 bps maker default

# Funding rate (per funding interval, not per tick). Positive = longs pay.
FUNDING_BPS_PER_INTERVAL = float(os.getenv("ENC3D_FUNDING_BPS", "0.0"))

# How many ticks between funding charges. 0 → funding disabled.
FUNDING_EVERY_N_TICKS = int(os.getenv("ENC3D_FUNDING_N_TICKS", "0"))


def bps_to_decimal(bps: float) -> float:
    return float(bps) * 1e-4


def fee_decimal_for(is_taker: bool = True) -> float:
    """Return the per-side fee in decimal (e.g. 5 bps → 0.0005)."""
    bps = FEE_BPS_TAKER if is_taker else FEE_BPS_MAKER
    return bps_to_decimal(bps)


def apply_fee_decimal(notional: float, fee_dec: float) -> float:
    """
    Compute fee amount in quote currency given notional and fee (decimal).
    Example: notional=100 USD, fee_dec=0.0005 → 0.05 USD fee.
    """
    return abs(float(notional)) * float(fee_dec)


# -----------------------------------------------------------------------------
# Funding (per-interval; caller decides when to invoke)
# -----------------------------------------------------------------------------
def funding_charge_decimal(position_notional: float, funding_bps: float | None = None) -> float:
    """
    Apply funding on a position's current notional. Returns the funding
    charge in quote currency (positive = creature pays, negative = creature
    receives).

    Kept intentionally simple — funding direction is provided by caller.
    """
    bps = FUNDING_BPS_PER_INTERVAL if funding_bps is None else float(funding_bps)
    return float(position_notional) * bps_to_decimal(bps)


def should_charge_funding(tick: int) -> bool:
    """True if this tick is a funding boundary."""
    if FUNDING_EVERY_N_TICKS <= 0:
        return False
    return (tick > 0) and (tick % FUNDING_EVERY_N_TICKS == 0)
