"""
World3D — the main tick-by-tick loop of the Reef.

For each generation:
  - Initialize (or reproduce from) a population of creatures.
  - For each tick t in the world stream:
      * compute features from snapshot@t
      * classify regime
      * record extreme event to tail bank if |ret| > 2σ
      * for each alive creature:
          - compute Z pressure
          - decide (action, size) via actions.decide
          - apply execution via simulator.step_execution
          - record trajectory step
          - check ruin — if dead, log to tail bank
  - Rank survivors, pick champion, persist state, start next generation.

The loop never peeks at future snapshots. features[t] is computed from
snapshot[t] which carries `recent_returns` up to t. No replay bias.
"""
from __future__ import annotations
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import (
    CREATURES_FILE,
    CHAMPION_FILE,
    WORLD_LOG_FILE,
)
from ..creatures import random_genome, GENE_BOUNDS
from ..creatures.creature import Creature, INITIAL_CAPITAL
from ..creatures.actions import decide
from ..market import (
    compute_features,
    classify_regime,
    BookStream,
    synthetic_book_stream,
)
from ..execution import step_execution
from ..execution.slippage import execution_pressure_z
from .tail_bank import TailBank
from .selection import rank_population
from .reproduction import next_generation


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
@dataclass
class WorldConfig:
    pop_size: int = 50
    ticks_per_gen: int = 2000
    n_generations: int = 3
    days_per_gen: float = 1.0       # used to annualize log-growth
    elite_frac: float = 0.20
    fresh_frac: float = 0.10
    mutation_rate: float = 0.25
    extreme_k_sigma: float = 2.0
    seed: int = 42
    symbol: str = "ADAUSDT"
    # If True and load_symbol_data returns None, fall back to synthetic.
    allow_synthetic_fallback: bool = True


@dataclass
class GenerationResult:
    generation: int
    n_alive_at_end: int
    n_dead: int
    champion_id: str | None
    champion_fitness: float
    champion_equity: float
    deaths_recorded: int
    extremes_recorded: int
    wall_seconds: float
    ranked_summary: list[dict] = field(default_factory=list)


# -----------------------------------------------------------------------------
# World
# -----------------------------------------------------------------------------
class World3D:
    def __init__(self, config: WorldConfig, book: BookStream | None = None):
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.tail_bank = TailBank()
        self.book = book if book is not None else self._load_book()
        # Cooldown tracking per creature (last exit tick)
        self._last_exit: dict[str, int] = {}

    # -------------------------------------------------------------------
    # Book loading
    # -------------------------------------------------------------------
    def _load_book(self) -> BookStream:
        """Try real data, fallback to synthetic if configured."""
        try:
            from ..market.book import load_symbol_data
            df = load_symbol_data(self.cfg.symbol)
        except Exception:
            df = None
        if df is not None and hasattr(df, "__len__") and len(df) >= 100:
            return BookStream.from_dataframe(df)
        if not self.cfg.allow_synthetic_fallback:
            raise RuntimeError(
                f"No data for symbol {self.cfg.symbol} and synthetic fallback disabled"
            )
        self._log(f"no real data for {self.cfg.symbol} — using synthetic stream")
        return synthetic_book_stream(n_ticks=self.cfg.ticks_per_gen, seed=self.cfg.seed)

    # -------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        try:
            WORLD_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with WORLD_LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        print(line, end="")

    # -------------------------------------------------------------------
    # Population init
    # -------------------------------------------------------------------
    def _init_population(self) -> list[Creature]:
        return [
            Creature.spawn(
                random_genome(self.rng),
                birth_tick=0,
                initial_capital=INITIAL_CAPITAL,
            )
            for _ in range(self.cfg.pop_size)
        ]

    # -------------------------------------------------------------------
    # Single generation
    # -------------------------------------------------------------------
    def run_generation(
        self,
        generation: int,
        population: list[Creature],
    ) -> GenerationResult:
        t0 = time.perf_counter()
        deaths = 0
        extremes = 0
        self._last_exit.clear()

        max_ticks = min(self.cfg.ticks_per_gen, len(self.book))
        if max_ticks <= 0:
            raise RuntimeError("Book stream is empty — cannot run a generation")

        for t in range(max_ticks):
            snap = self.book.at(t)
            if snap is None:
                break
            feats = compute_features(snap)
            regime = classify_regime(feats)

            # Record extreme tick (market-level) to tail bank
            ret = float(feats.get("ret_recent", 0.0))
            sigma = float(feats.get("ret_sigma", 0.0))
            if sigma > 0 and abs(ret) > self.cfg.extreme_k_sigma * sigma:
                self.tail_bank.record_extreme(
                    tick=t, ret=ret, sigma=sigma, regime=regime,
                    k_sigma=self.cfg.extreme_k_sigma,
                )
                extremes += 1

            is_spike = bool(feats.get("is_vol_spike", False))
            mid = float(feats.get("mid_price", 0.0))

            for c in population:
                if not c.alive:
                    continue

                # Z pressure for a hypothetical entry sized at kelly_cap * capital
                size_usd = float(c.capital) * float(c.genes.get("kelly_cap", 0.15))
                z = execution_pressure_z(feats, size_usd=size_usd)

                last_exit_tick = self._last_exit.get(c.id, -10 ** 9)
                action, size_frac = decide(
                    c, t, feats, regime, z_pressure=z, last_exit_tick=last_exit_tick,
                )

                # Execute
                info = step_execution(c, t, action, size_frac, feats)
                if info["filled"] and action in ("SELL_FULL", "SELL_PARTIAL"):
                    self._last_exit[c.id] = t

                # Compute tick return for convexity accounting (per-creature)
                # Use change in creature equity over this tick as the return signal.
                if c.trajectory:
                    prev_eq = c.trajectory[-1][1]
                    cur_eq = c.equity(mid)
                    tick_return = 0.0 if prev_eq <= 0 else (cur_eq - prev_eq) / prev_eq
                else:
                    tick_return = 0.0

                c.record_step(
                    tick=t,
                    mark_price=mid,
                    z_pressure=info.get("z_pressure", z),
                    is_vol_spike=is_spike,
                    tick_return=tick_return,
                )

                # Ruin check
                if c.check_ruin(mid):
                    c.kill(tick=t, reason="ruin")
                    self.tail_bank.record_death(
                        c,
                        reason="ruin",
                        context={"mid": mid, "regime": regime, "z": z},
                    )
                    deaths += 1

        # End of generation — mark survivors as reason="end" (don't kill them)
        alive = [c for c in population if c.alive]

        # Rank and pick champion
        ranked = rank_population(
            population,
            world_ticks=max_ticks,
            days_total=self.cfg.days_per_gen,
            tail_bank_events=self.tail_bank.deaths(),
            gene_bounds=GENE_BOUNDS,
        )

        champion_id = None
        champion_fitness = float("-inf")
        champion_equity = 0.0
        if ranked:
            top = ranked[0]
            champ = top["creature"]
            champion_id = champ.id
            champion_fitness = float(top["fitness"])
            champion_equity = float(top["components"]["final_equity"])
            self._save_champion(champ, top)

        self._save_state(population)

        summary_rows: list[dict] = []
        for e in ranked[:10]:
            c = e["creature"]
            summary_rows.append({
                "id": c.id,
                "alive": c.alive,
                "fitness": e["fitness"],
                "final_equity": e["components"]["final_equity"],
                "max_drawdown": c.max_drawdown,
                "n_trades": len([t for t in c.trades if t["side"] == "sell"]),
                "survival": e["survival"],
                "convexity": e["convexity"],
            })

        wall = time.perf_counter() - t0

        return GenerationResult(
            generation=generation,
            n_alive_at_end=len(alive),
            n_dead=self.cfg.pop_size - len(alive),
            champion_id=champion_id,
            champion_fitness=champion_fitness,
            champion_equity=champion_equity,
            deaths_recorded=deaths,
            extremes_recorded=extremes,
            wall_seconds=wall,
            ranked_summary=summary_rows,
        )

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------
    def _save_state(self, population: list[Creature]) -> None:
        CREATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CREATURES_FILE.open("w", encoding="utf-8") as f:
            for c in population:
                f.write(c.to_jsonl() + "\n")

    def _save_champion(self, creature: Creature, rank_entry: dict) -> None:
        CHAMPION_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "summary": creature.summary(),
            "fitness": rank_entry["fitness"],
            "survival": rank_entry["survival"],
            "convexity": rank_entry["convexity"],
            "components": rank_entry["components"],
            "trajectory_len": len(creature.trajectory),
        }
        CHAMPION_FILE.write_text(
            json.dumps(payload, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )

    # -------------------------------------------------------------------
    # Multi-gen run
    # -------------------------------------------------------------------
    def run(self) -> list[GenerationResult]:
        population = self._init_population()
        results: list[GenerationResult] = []

        for gen in range(self.cfg.n_generations):
            self._log(
                f"=== gen {gen+1}/{self.cfg.n_generations} "
                f"pop={len(population)} ticks={min(self.cfg.ticks_per_gen, len(self.book))} ==="
            )
            res = self.run_generation(gen + 1, population)
            results.append(res)
            self._log(
                f"gen {gen+1} done in {res.wall_seconds:.1f}s  "
                f"alive={res.n_alive_at_end}/{self.cfg.pop_size}  "
                f"deaths={res.deaths_recorded}  extremes={res.extremes_recorded}  "
                f"champ_fitness={res.champion_fitness:.4f}  champ_eq={res.champion_equity:.2f}"
            )

            if gen + 1 < self.cfg.n_generations:
                # Reproduce
                ranked = rank_population(
                    population,
                    world_ticks=min(self.cfg.ticks_per_gen, len(self.book)),
                    days_total=self.cfg.days_per_gen,
                    tail_bank_events=self.tail_bank.deaths(),
                    gene_bounds=GENE_BOUNDS,
                )
                population = next_generation(
                    ranked,
                    pop_size=self.cfg.pop_size,
                    birth_tick=0,
                    elite_frac=self.cfg.elite_frac,
                    fresh_frac=self.cfg.fresh_frac,
                    mutation_rate=self.cfg.mutation_rate,
                    rng=self.rng,
                )

        return results
