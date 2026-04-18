"""
Slippage + execution pressure (the Z-axis of the Reef).

Z ∈ [0, 1] is the execution-pressure coordinate for a given creature at a
given tick. It is NOT decorative — it enters:
  - actions.decide() as a gate (`max_pressure_z`) and as a size shrinker
  - fills.simulate_fill() as a slippage multiplier on fill price
  - fitness.convexity_bonus() as the regime indicator for tail counting

Components (all in [0, 1] after normalization):
  - spread_component     : spread_decimal / REF_SPREAD (clipped at 1)
  - depth_component      : 1 − min(depth_available / creature_size_demand, 1)
  - volatility_component : σ_recent / REF_SIGMA (clipped at 1)
  - size_component       : creature_size_demand / REF_SIZE (clipped at 1)

Weighted combination with weights that sum to 1. Defaults can be tuned
per-symbol later.
"""
from __future__ import annotations
import os

# Reference scales (override via env for per-venue tuning)
REF_SPREAD = float(os.getenv("ENC3D_REF_SPREAD", "0.0010"))   # 10 bps "normal" spread
REF_SIGMA = float(os.getenv("ENC3D_REF_SIGMA", "0.005"))      # 50 bps σ per-window
REF_SIZE_USD = float(os.getenv("ENC3D_REF_SIZE_USD", "1000.0"))   # $1k order is "normal"

W_SPREAD = float(os.getenv("ENC3D_W_SPREAD", "0.30"))
W_DEPTH = float(os.getenv("ENC3D_W_DEPTH", "0.30"))
W_VOL = float(os.getenv("ENC3D_W_VOL", "0.20"))
W_SIZE = float(os.getenv("ENC3D_W_SIZE", "0.20"))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def execution_pressure_z(
    features: dict,
    size_usd: float = 0.0,
) -> float:
    """
    Compute the Z-pressure for a creature wanting to trade `size_usd` at this
    book state.

    Features expected (all decimal unless noted):
      - spread_decimal          : (ask - bid) / mid
      - ret_sigma               : recent return σ (decimal)
      - available_depth_usd     : usable $ depth on the relevant side
                                  (if 0 or missing, treated as full depletion)
    """
    spread = float(features.get("spread_decimal", REF_SPREAD))
    sigma = float(features.get("ret_sigma", REF_SIGMA))
    depth_usd = float(features.get("available_depth_usd", REF_SIZE_USD))
    size = max(0.0, float(size_usd))

    c_spread = _clip01(spread / REF_SPREAD) if REF_SPREAD > 0 else 0.0
    c_vol = _clip01(sigma / REF_SIGMA) if REF_SIGMA > 0 else 0.0

    if size > 0:
        depth_ratio = depth_usd / max(size, 1.0)
        c_depth = _clip01(1.0 - min(depth_ratio, 1.0))
    else:
        c_depth = 0.0

    c_size = _clip01(size / REF_SIZE_USD) if REF_SIZE_USD > 0 else 0.0

    z = (
        W_SPREAD * c_spread
        + W_DEPTH * c_depth
        + W_VOL * c_vol
        + W_SIZE * c_size
    )
    return _clip01(z)


def slippage_decimal(z: float, side: str = "buy") -> float:
    """
    Return slippage as a DECIMAL fraction of mid-price.
    Buy → mid × (1 + slippage). Sell → mid × (1 - slippage).

    At Z=0  → 0 bps.
    At Z=1  → 30 bps slippage (on top of spread, which is paid separately
               through the book).

    Calibration target: in calm markets slippage is negligible, in hostile
    ones (spike + thin depth + big size) it compounds with spread to produce
    realistic adverse fills.
    """
    z = _clip01(z)
    # Quadratic growth — big blowups only in the top quartile of Z
    return (z ** 2) * 0.0030
