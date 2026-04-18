"""
Actions — tick-by-tick decision policy.

Rules enforced here:
  - NO LOOKAHEAD: decide() receives only book_snapshot @ t and features
    computed from [t - lookback, t]. It never sees t+1.
  - ORTHOGONAL SIGNALS: the four signal_mix modes combine decorrelated
    indicators (momentum z-score, mean-reversion residual, range-expansion,
    imbalance). We avoid the "signal correlation trap" by normalizing each
    component separately and combining with a mode-specific weighting.
  - PRESSURE GATE: if Z >= max_pressure_z, creature skips ENTRY (exits are
    still allowed — you always need to be able to leave).
  - COOLDOWN: creature cannot re-enter within cooldown_ticks of last exit.

Returns a tuple (action, size_fraction) where:
  action ∈ {"HOLD", "BUY", "SELL_FULL", "SELL_PARTIAL"}
  size_fraction ∈ [0, kelly_cap] — fraction of current cash to deploy on BUY.
"""
from __future__ import annotations
import math
from typing import Literal

Action = Literal["HOLD", "BUY", "SELL_FULL", "SELL_PARTIAL"]


def _z_momentum(features: dict) -> float:
    """Z-score of recent return vs lookback. +1σ = strong up, −1σ = strong down."""
    ret = float(features.get("ret_recent", 0.0))
    sigma = float(features.get("ret_sigma", 0.0))
    if sigma <= 1e-12:
        return 0.0
    z = ret / sigma
    # Clip to keep downstream math bounded
    return max(-3.0, min(3.0, z))


def _mean_rev_score(features: dict) -> float:
    """
    Mean-reversion residual: +1 = price below mean (buy), −1 = above.
    Uses microprice - SMA, scaled by sigma.
    """
    residual = float(features.get("mean_rev_residual", 0.0))
    sigma = float(features.get("ret_sigma", 1e-6))
    if sigma <= 1e-12:
        return 0.0
    return max(-3.0, min(3.0, -residual / sigma))


def _breakout_score(features: dict) -> float:
    """Range-expansion breakout: +1 = price broke above high, −1 = below low."""
    range_z = float(features.get("range_expansion_z", 0.0))
    return max(-3.0, min(3.0, range_z))


def _imbalance_score(features: dict, threshold: float) -> float:
    """OBI score gated by threshold. Returns signed in [-1, +1]."""
    obi = float(features.get("order_book_imbalance", 0.0))  # in [-1, +1]
    if abs(obi) < threshold:
        return 0.0
    return max(-1.0, min(1.0, obi))


def signal_score(genes: dict, features: dict) -> float:
    """
    Combine orthogonal components per signal_mix. Returns signed score in
    roughly [-1, +1] after tanh compression — think of it as "bull belief".
    """
    mix = genes.get("signal_mix", "adaptive")
    imb_thr = float(genes.get("imbalance_threshold", 0.1))

    zm = _z_momentum(features)          # in [-3, +3]
    mr = _mean_rev_score(features)      # in [-3, +3]
    br = _breakout_score(features)      # in [-3, +3]
    imb = _imbalance_score(features, imb_thr)  # in [-1, +1]

    if mix == "momentum":
        raw = 0.6 * zm + 0.2 * br + 0.2 * imb
    elif mix == "mean_rev":
        raw = 0.7 * mr + 0.3 * imb
    elif mix == "breakout":
        raw = 0.5 * br + 0.3 * zm + 0.2 * imb
    else:  # adaptive — weighted by recent volatility regime
        vol_regime = float(features.get("vol_regime", 0.5))  # [0, 1], high = volatile
        raw = (
            (1 - vol_regime) * (0.5 * mr + 0.5 * zm)   # calm → mix of momentum + MR
            + vol_regime * (0.6 * br + 0.4 * imb)       # volatile → breakout + imbalance
        )
    # tanh compresses to [-1, +1]
    return math.tanh(raw / 2.0)


def confidence(genes: dict, features: dict) -> float:
    """
    Confidence in [0, 1] — function of |signal_score| and spread penalty.
    Wider spread → lower confidence.
    """
    s = abs(signal_score(genes, features))
    spread = float(features.get("spread_decimal", 0.0))
    spread_sens = float(genes.get("spread_sensitivity", 1.0))
    spread_penalty = max(0.0, min(1.0, spread_sens * spread * 100.0))
    conf = s * (1.0 - spread_penalty)
    return max(0.0, min(1.0, conf))


def regime_allows(genes: dict, current_regime: str) -> bool:
    """Does the creature's regime_preference allow trading in current_regime?"""
    pref = genes.get("regime_preference", "any")
    if pref == "any":
        return True
    return pref == current_regime


# -----------------------------------------------------------------------------
# Main decision
# -----------------------------------------------------------------------------
def decide(
    creature,
    tick: int,
    features: dict,
    current_regime: str,
    z_pressure: float,
    last_exit_tick: int = -10**9,
) -> tuple[Action, float]:
    """
    Decide the action for this creature at this tick.

    Returns (action, size_fraction):
      - If action == "BUY", size_fraction is fraction of CASH to deploy,
        already capped at kelly_cap.
      - If action == "SELL_FULL", size_fraction is 1.0 (full exit).
      - Otherwise size_fraction is 0.
    """
    if not creature.alive:
        return ("HOLD", 0.0)

    genes = creature.genes

    # ------------- EXIT CHECKS FIRST (always allowed, no Z gate) -------
    if creature.position > 0 and creature.entry_price > 0:
        # Stop loss / take profit on mark price (unrealized pnl)
        mark = float(features.get("mid_price", creature.entry_price))
        upnl = (mark - creature.entry_price) / creature.entry_price
        sl = float(genes.get("stop_loss_pct", 0.02))
        tp = float(genes.get("take_profit_pct", 0.03))
        if upnl <= -sl:
            return ("SELL_FULL", 1.0)
        if upnl >= tp:
            return ("SELL_FULL", 1.0)
        # Time stop
        time_stop = int(genes.get("time_stop_ticks", 200))
        if creature.entry_tick >= 0 and (tick - creature.entry_tick) >= time_stop:
            return ("SELL_FULL", 1.0)

    # ------------- ENTRY GATES (pressure, cooldown, regime, confidence) ----
    if creature.position > 0:
        return ("HOLD", 0.0)  # MVP: no pyramiding. One position at a time.

    max_z = float(genes.get("max_pressure_z", 0.7))
    if z_pressure > max_z:
        return ("HOLD", 0.0)

    cooldown = int(genes.get("cooldown_ticks", 20))
    if tick - last_exit_tick < cooldown:
        return ("HOLD", 0.0)

    if not regime_allows(genes, current_regime):
        return ("HOLD", 0.0)

    # Signal + confidence
    s = signal_score(genes, features)
    conf = confidence(genes, features)
    min_conf = float(genes.get("min_confidence", 0.5))
    if conf < min_conf:
        return ("HOLD", 0.0)
    if s <= 0:
        return ("HOLD", 0.0)  # MVP: long-only

    # Size: Kelly cap × confidence × (1 − Z), so pressure shrinks size too
    kelly_cap = float(genes.get("kelly_cap", 0.15))
    size_fraction = kelly_cap * conf * max(0.0, (1.0 - z_pressure))
    size_fraction = max(0.0, min(kelly_cap, size_fraction))

    if size_fraction < 1e-4:
        return ("HOLD", 0.0)

    return ("BUY", size_fraction)
