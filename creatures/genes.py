"""
Genes — bounds, random sampling, mutation, crossover, and normalized distance.

Genes are split into families so we can mutate/read them coherently:
  PERCEPTION   — how the creature reads the book
  DECISION     — when it acts
  RISK         — how big it bets and when it stops
  EXECUTION    — how it responds to execution pressure (Z-axis)
  REGIME       — regime preference (categorical)
  SURVIVAL     — ruin threshold and recovery behavior

All numeric genes are TUPLES (lo, hi). All categorical genes are LISTS.
normalized_distance() uses this convention to produce a value in [0, 1].
"""
from __future__ import annotations
import random
from typing import Any

# -----------------------------------------------------------------------------
# GENE_BOUNDS
# -----------------------------------------------------------------------------
# Numeric bounds are (lo, hi) tuples. Categorical bounds are list[str].
GENE_BOUNDS: dict[str, Any] = {
    # PERCEPTION ---------------------------------------------------------
    "lookback_ticks":        (20, 200),     # window for features
    "spread_sensitivity":    (0.1, 5.0),    # penalty multiplier on spread
    "imbalance_threshold":   (0.05, 0.50),  # OBI threshold to count as signal
    "depth_decay_lambda":    (0.5, 5.0),    # how fast depth decays in signal mix
    # DECISION -----------------------------------------------------------
    "min_confidence":        (0.20, 0.90),  # gate for buy/sell (decimal)
    "signal_mix":            ["momentum", "mean_rev", "breakout", "adaptive"],
    # RISK ---------------------------------------------------------------
    "kelly_cap":             (0.05, 0.25),  # max position fraction of capital
    "stop_loss_pct":         (0.005, 0.050),
    "take_profit_pct":       (0.010, 0.100),
    "time_stop_ticks":       (50, 500),
    # EXECUTION ----------------------------------------------------------
    "max_pressure_z":        (0.30, 0.90),  # skip entry if Z > this
    "cooldown_ticks":        (5, 100),
    # REGIME -------------------------------------------------------------
    "regime_preference":     ["trending", "mean_rev", "volatile", "any"],
    # SURVIVAL -----------------------------------------------------------
    "ruin_threshold_pct":    (0.30, 0.70),  # die if equity < initial * this
}


# -----------------------------------------------------------------------------
# Random genome
# -----------------------------------------------------------------------------
def random_genome(rng: random.Random | None = None) -> dict:
    """Sample a uniform random genome within bounds."""
    r = rng if rng is not None else random
    genes: dict = {}
    for key, bound in GENE_BOUNDS.items():
        if isinstance(bound, list):
            genes[key] = r.choice(bound)
        else:
            lo, hi = bound
            if isinstance(lo, int) and isinstance(hi, int):
                genes[key] = r.randint(lo, hi)
            else:
                genes[key] = r.uniform(float(lo), float(hi))
    return genes


# -----------------------------------------------------------------------------
# Mutation
# -----------------------------------------------------------------------------
def mutate(genes: dict, rate: float = 0.25, rng: random.Random | None = None) -> dict:
    """
    With probability `rate`, perturb each gene:
      - numeric: gaussian step of 10% of the bound range, clipped to bounds.
      - categorical: resample uniformly from the list.
    """
    r = rng if rng is not None else random
    out: dict = dict(genes)
    for key, bound in GENE_BOUNDS.items():
        if r.random() > rate:
            continue
        if isinstance(bound, list):
            out[key] = r.choice(bound)
            continue
        lo, hi = bound
        cur = out.get(key, (lo + hi) / 2)
        span = float(hi) - float(lo)
        step = r.gauss(0.0, 0.10) * span
        new_val = cur + step
        if isinstance(lo, int) and isinstance(hi, int):
            new_val = int(round(new_val))
            new_val = max(lo, min(hi, new_val))
        else:
            new_val = max(float(lo), min(float(hi), float(new_val)))
        out[key] = new_val
    return out


# -----------------------------------------------------------------------------
# Crossover (uniform)
# -----------------------------------------------------------------------------
def crossover(genes_a: dict, genes_b: dict, rng: random.Random | None = None) -> dict:
    """Uniform crossover — each gene picked from a or b with 50/50."""
    r = rng if rng is not None else random
    return {
        k: (genes_a.get(k) if r.random() < 0.5 else genes_b.get(k))
        for k in GENE_BOUNDS
    }


# -----------------------------------------------------------------------------
# Normalized distance (for tail penalty)
# -----------------------------------------------------------------------------
def normalized_distance(
    genes_a: dict,
    genes_b: dict,
    bounds: dict | None = None,
) -> float:
    """
    Average normalized distance between two gene dicts.

    Numeric gene: |a - b| / (hi - lo) clipped to [0, 1].
    Categorical gene: 0 if equal, 1 if different.

    Missing genes on either side are skipped. Returns +inf if no
    comparable genes (which callers should treat as "no similarity signal").
    """
    b = bounds if bounds is not None else GENE_BOUNDS
    if not genes_a or not genes_b:
        return float("inf")
    total = 0.0
    count = 0
    for key, bound in b.items():
        if key not in genes_a or key not in genes_b:
            continue
        va, vb = genes_a[key], genes_b[key]
        if isinstance(bound, list):
            total += 0.0 if va == vb else 1.0
        else:
            lo, hi = bound
            span = float(hi) - float(lo)
            if span <= 0:
                continue
            d = abs(float(va) - float(vb)) / span
            total += min(1.0, max(0.0, d))
        count += 1
    if count == 0:
        return float("inf")
    return total / count
