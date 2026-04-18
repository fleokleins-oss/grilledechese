"""
Reproduction — produce the next generation from ranked survivors.

Strategy:
  - Elite: top `elite_frac` copied verbatim (no mutation, no crossover).
  - Breed: remaining slots filled by crossover of two elites + mutation.
  - Fresh: a small `fresh_frac` of fully random genomes to keep diversity
           (especially useful when the tail_bank is growing and penalizing
           clusters).
"""
from __future__ import annotations
import random

from ..creatures import random_genome, mutate, crossover
from ..creatures.creature import Creature, INITIAL_CAPITAL


def next_generation(
    ranked: list[dict],
    pop_size: int,
    birth_tick: int,
    elite_frac: float = 0.20,
    fresh_frac: float = 0.10,
    mutation_rate: float = 0.25,
    rng: random.Random | None = None,
) -> list[Creature]:
    """
    Build the next generation.

    Args:
      ranked:       output of selection.rank_population (best first)
      pop_size:     desired size of the new generation
      birth_tick:   tick at which the new creatures are born
      elite_frac:   fraction of population preserved verbatim
      fresh_frac:   fraction of fully random new genomes
      mutation_rate: per-gene mutation prob on bred genomes
      rng:          optional Random instance for reproducibility
    """
    r = rng if rng is not None else random
    if not ranked:
        return [Creature.spawn(random_genome(r), birth_tick=birth_tick) for _ in range(pop_size)]

    elite_n = max(1, int(pop_size * elite_frac))
    fresh_n = max(0, int(pop_size * fresh_frac))
    breed_n = max(0, pop_size - elite_n - fresh_n)

    elites = [e["creature"] for e in ranked[:elite_n]]
    new_pop: list[Creature] = []

    # Elites reborn with fresh capital (keep genes, reset state)
    for e in elites:
        new_pop.append(
            Creature.spawn(
                dict(e.genes),
                birth_tick=birth_tick,
                parent_ids=(e.id,),
                initial_capital=INITIAL_CAPITAL,
            )
        )
        if len(new_pop) >= pop_size:
            return new_pop

    # Bred — pick two elites, crossover, mutate
    breeding_pool = elites if elites else [Creature.spawn(random_genome(r))]
    for _ in range(breed_n):
        p1 = r.choice(breeding_pool)
        p2 = r.choice(breeding_pool)
        child_genes = crossover(p1.genes, p2.genes, rng=r)
        child_genes = mutate(child_genes, rate=mutation_rate, rng=r)
        new_pop.append(
            Creature.spawn(
                child_genes,
                birth_tick=birth_tick,
                parent_ids=(p1.id, p2.id),
                initial_capital=INITIAL_CAPITAL,
            )
        )
        if len(new_pop) >= pop_size:
            return new_pop

    # Fresh randoms — diversity injection
    for _ in range(fresh_n):
        new_pop.append(
            Creature.spawn(
                random_genome(r),
                birth_tick=birth_tick,
                initial_capital=INITIAL_CAPITAL,
            )
        )
        if len(new_pop) >= pop_size:
            break

    # Top up if we didn't reach pop_size (rounding)
    while len(new_pop) < pop_size:
        new_pop.append(
            Creature.spawn(
                random_genome(r),
                birth_tick=birth_tick,
                initial_capital=INITIAL_CAPITAL,
            )
        )

    return new_pop
