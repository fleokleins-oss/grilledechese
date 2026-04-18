"""
Regime classification — labels a tick's market regime from its features.

Four regimes, matching GENE_BOUNDS["regime_preference"]:
  - trending    : strong signed momentum, moderate vol
  - mean_rev    : residual far from mean, low |momentum|
  - volatile    : high σ / vol regime, regardless of direction
  - any         : fallback label (not used by classifier; only a gene value)

Classification rules (soft thresholds, priority order):
  1. if vol_regime > 0.7                     → "volatile"
  2. if |range_expansion_z| > 1.5            → "trending"
  3. if |mean_rev_residual| > 0.002          → "mean_rev"
  4. else                                    → "trending"  (default)
"""
from __future__ import annotations

REGIMES = ("trending", "mean_rev", "volatile")


def classify_regime(features: dict) -> str:
    vol_regime = float(features.get("vol_regime", 0.0))
    if vol_regime > 0.7:
        return "volatile"

    rz = float(features.get("range_expansion_z", 0.0))
    if abs(rz) > 1.5:
        return "trending"

    mrr = float(features.get("mean_rev_residual", 0.0))
    if abs(mrr) > 0.002:
        return "mean_rev"

    return "trending"
