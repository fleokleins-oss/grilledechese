"""
Selection — ranking population by survival and convexity.

Multi-criteria dominance rather than a single scalar, then tie-breaking
on fitness. This is Taleb-flavored: a creature that lived long with small
DD and positive convexity dominates one with higher P&L but shorter life.

Primary sort key:
  1. alive > dead
  2. larger survival_score
  3. larger convexity_score
  4. larger fitness.fitness (the master scalar from creatures/fitness.py)
"""
from __future__ import annotations
from typing import Iterable

from ..creatures.fitness import compute_fitness, survival_factor, convexity_bonus


def survival_score(creature, world_ticks: int) -> float:
    """Wrap creatures.fitness.survival_factor for selection."""
    return float(survival_factor(creature, world_ticks))


def convexity_score(creature) -> float:
    """Positive if creature shows Taleb-convex behavior in spikes."""
    return float(convexity_bonus(creature))


def rank_population(
    creatures: Iterable,
    world_ticks: int,
    days_total: float,
    tail_bank_events: list | None = None,
    gene_bounds: dict | None = None,
) -> list[dict]:
    """
    Rank a population and return a sorted list of dicts:
      {
        "creature":    <Creature>,
        "survival":    float,
        "convexity":   float,
        "fitness":     float,           # master scalar
        "components":  dict,            # full compute_fitness output
      }

    Sorted best-first.
    """
    entries: list[dict] = []
    events = list(tail_bank_events) if tail_bank_events else []
    for c in creatures:
        comps = compute_fitness(
            c,
            world_ticks=world_ticks,
            days_total=days_total,
            tail_bank_events=events,
            gene_bounds=gene_bounds,
        )
        entries.append({
            "creature": c,
            "survival": survival_score(c, world_ticks),
            "convexity": convexity_score(c),
            "fitness": comps["fitness"],
            "components": comps,
        })

    def key(e: dict) -> tuple:
        c = e["creature"]
        # alive first (True = 1), then survival, convexity, fitness — all descending
        return (
            1 if c.alive else 0,
            e["survival"],
            e["convexity"],
            e["fitness"],
        )

    entries.sort(key=key, reverse=True)
    return entries
