"""
Fitness — dimensionally coherent, in decimal, with local tail penalty.

Formula:
  fitness = log_growth_annual × regime_factor × survival_factor − tail_penalty_local

Where:
  log_growth_annual  : log(final_eq / initial_capital) × (365 / days_lived)
                       — in DECIMAL, annualized, NEVER bps.
  regime_factor      : weighted average of performance-across-regimes.
                       Penalizes single-regime specialists.
  survival_factor    : alive bonus + uptime × (1 − max_drawdown).
                       Alive at end × long uptime × small DD = close to 1.
                       Dead early with big DD = close to 0.
  tail_penalty_local : proportional to how CLOSE the genome is to tail_bank
                       events (genome similarity to killed creatures). Far
                       genomes get zero penalty.

A convexity bonus is added when the creature performs POSITIVELY during
vol-spike ticks (Taleb: winning asymmetrically in tails > winning in calm).
"""
from __future__ import annotations
import math
from typing import Iterable
import numpy as np

from .genes import GENE_BOUNDS, normalized_distance

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
TAIL_DISTANCE_THRESHOLD = 0.15   # below this normalized distance, penalty kicks in
TAIL_BANK_MIN_EVENTS = 10        # too few events → no penalty (unreliable signal)
CONVEXITY_BONUS_MAX = 0.20       # up to +20% fitness for strong tail-convexity


# -----------------------------------------------------------------------------
# Survival factor
# -----------------------------------------------------------------------------
def survival_factor(creature, world_ticks: int) -> float:
    """
    Survival score in [0, 1]:
      - Fraction of world ticks the creature was alive.
      - Scaled down by max_drawdown (worst equity trough).
      - Bonus +10% if still alive at end.
    """
    if world_ticks <= 0:
        return 0.0
    uptime = min(1.0, creature.ticks_alive / world_ticks)
    dd = max(0.0, min(1.0, creature.max_drawdown))
    raw = uptime * (1.0 - dd)
    if creature.alive:
        raw *= 1.10
    return max(0.0, min(1.0, raw))


# -----------------------------------------------------------------------------
# Regime factor
# -----------------------------------------------------------------------------
def regime_factor(creature, regime_counts: dict[str, int] | None = None) -> float:
    """
    Currently MVP: returns a flat 1.0 for creatures with "any" regime, and
    a modest 0.85 penalty for specialists — because specializing to a
    regime that dominated the sample is survivorship bias we don't want.
    When we have per-regime P&L attribution, replace with a proper
    weighted performance across regimes.
    """
    pref = creature.genes.get("regime_preference", "any")
    if pref == "any":
        return 1.0
    return 0.85


# -----------------------------------------------------------------------------
# Convexity bonus (Taleb)
# -----------------------------------------------------------------------------
def convexity_bonus(creature) -> float:
    """
    Bonus ∈ [0, CONVEXITY_BONUS_MAX] for positive performance during
    vol-spike ticks. If the creature never saw a spike or had net-negative
    spike return, bonus is 0.
    """
    if creature.ticks_in_vol_spike <= 0:
        return 0.0
    avg_spike_ret = creature.return_in_vol_spike / max(1, creature.ticks_in_vol_spike)
    if avg_spike_ret <= 0:
        return 0.0
    # Cap the bonus linearly; 1% avg per-spike return → full bonus
    scale = min(1.0, avg_spike_ret / 0.01)
    return CONVEXITY_BONUS_MAX * scale


# -----------------------------------------------------------------------------
# Tail penalty (LOCAL — depends on genome similarity to graveyard)
# -----------------------------------------------------------------------------
def tail_penalty_local(
    genes: dict,
    tail_bank_events: Iterable[dict],
    gene_bounds: dict | None = None,
    threshold: float = TAIL_DISTANCE_THRESHOLD,
    min_events: int = TAIL_BANK_MIN_EVENTS,
) -> float:
    """
    Penalty in [0, 0.5] proportional to how close the genome is to the
    *nearest* tail_bank events.

    For each event, compute normalized_distance(genes, event.genes).
    Take the top K=5 closest events. If mean distance < threshold, penalty
    scales as (threshold - mean_d) / threshold, up to 0.5.

    Returns 0.0 if the tail bank has fewer than `min_events`.
    """
    events = list(tail_bank_events)
    if len(events) < min_events:
        return 0.0
    bounds = gene_bounds if gene_bounds is not None else GENE_BOUNDS

    distances: list[float] = []
    for ev in events:
        ev_genes = ev.get("genes") if isinstance(ev, dict) else None
        if not ev_genes:
            continue
        d = normalized_distance(genes, ev_genes, bounds)
        if math.isfinite(d):
            distances.append(d)

    if not distances:
        return 0.0

    distances.sort()
    k = min(5, len(distances))
    mean_d = sum(distances[:k]) / k

    if mean_d >= threshold:
        return 0.0
    severity = (threshold - mean_d) / threshold  # in (0, 1]
    return min(0.5, 0.5 * severity)


# -----------------------------------------------------------------------------
# Master fitness
# -----------------------------------------------------------------------------
def compute_fitness(
    creature,
    world_ticks: int,
    days_total: float,
    tail_bank_events: Iterable[dict] | None = None,
    gene_bounds: dict | None = None,
) -> dict:
    """
    Returns a dict with all components and final fitness, so callers can
    inspect why a genome scored what it did.

    fitness = log_growth_annual × regime_factor × survival_factor
            × (1 + convexity_bonus)
            − tail_penalty_local
    """
    if not creature.trajectory or creature.initial_capital <= 0:
        return {
            "fitness": float("-inf"),
            "log_growth_annual": float("-inf"),
            "regime_factor": 0.0,
            "survival_factor": 0.0,
            "convexity_bonus": 0.0,
            "tail_penalty_local": 0.0,
            "total_return_decimal": -1.0,
            "final_equity": 0.0,
        }

    final_eq = float(creature.trajectory[-1][1])
    total_return = final_eq / creature.initial_capital  # e.g. 1.05 = +5%
    if total_return <= 0:
        # Death — log-growth undefined. Bottom-rank but finite so selection
        # can still compare against other dead creatures.
        log_growth_annual = -10.0
    else:
        days = max(1e-6, float(days_total))
        log_growth_annual = math.log(total_return) * (365.0 / days)

    reg_f = regime_factor(creature)
    surv_f = survival_factor(creature, world_ticks)
    conv_bonus = convexity_bonus(creature)
    tail_pen = 0.0
    if tail_bank_events is not None:
        tail_pen = tail_penalty_local(
            creature.genes, tail_bank_events, gene_bounds=gene_bounds
        )

    fitness = (
        log_growth_annual
        * reg_f
        * surv_f
        * (1.0 + conv_bonus)
        - tail_pen
    )

    return {
        "fitness": float(fitness),
        "log_growth_annual": float(log_growth_annual),
        "regime_factor": float(reg_f),
        "survival_factor": float(surv_f),
        "convexity_bonus": float(conv_bonus),
        "tail_penalty_local": float(tail_pen),
        "total_return_decimal": float(total_return - 1.0),
        "final_equity": float(final_eq),
    }
