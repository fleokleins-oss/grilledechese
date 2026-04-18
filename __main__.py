"""
CLI — run the Reef from one command.

Usage:
  python -m encruzilhada3d                       # defaults
  python -m encruzilhada3d --pop 100 --ticks 2000 --gens 5 --symbol ADAUSDT
  python -m encruzilhada3d --synthetic           # force synthetic stream
  python -m encruzilhada3d --render-html         # emit reef.html at end

All outputs go under ENC3D_STATE_ROOT (default ./state/encruzilhada3d).
"""
from __future__ import annotations
import argparse
import sys

from .engine.world3d import World3D, WorldConfig


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="encruzilhada3d", description="Reef — tick-by-tick survival ecosystem")
    p.add_argument("--pop", type=int, default=50, help="population size per generation")
    p.add_argument("--ticks", type=int, default=2000, help="ticks per generation")
    p.add_argument("--gens", type=int, default=3, help="number of generations")
    p.add_argument("--days", type=float, default=1.0, help="calendar days covered per generation (for annualization)")
    p.add_argument("--symbol", type=str, default="ADAUSDT", help="symbol for data loading")
    p.add_argument("--seed", type=int, default=42, help="RNG seed")
    p.add_argument("--synthetic", action="store_true", help="force synthetic stream even if data exists")
    p.add_argument("--render-html", action="store_true", help="render reef.html after run")
    args = p.parse_args(argv)

    cfg = WorldConfig(
        pop_size=args.pop,
        ticks_per_gen=args.ticks,
        n_generations=args.gens,
        days_per_gen=args.days,
        symbol=args.symbol,
        seed=args.seed,
        allow_synthetic_fallback=True,
    )

    book = None
    if args.synthetic:
        from .market.book import synthetic_book_stream
        book = synthetic_book_stream(n_ticks=args.ticks, seed=args.seed)

    w = World3D(cfg, book=book)
    # Keep a handle on the final population so we can render it.
    # World3D doesn't expose the final population directly; we replicate the
    # multi-gen loop here so we can reach in.
    final_population = w._init_population()
    results = []
    for gen in range(cfg.n_generations):
        w._log(f"=== gen {gen+1}/{cfg.n_generations} pop={len(final_population)} ===")
        res = w.run_generation(gen + 1, final_population)
        results.append(res)
        if gen + 1 < cfg.n_generations:
            from .engine.selection import rank_population
            from .engine.reproduction import next_generation
            from .creatures.genes import GENE_BOUNDS
            ranked = rank_population(
                final_population,
                world_ticks=min(cfg.ticks_per_gen, len(w.book)),
                days_total=cfg.days_per_gen,
                tail_bank_events=w.tail_bank.deaths(),
                gene_bounds=GENE_BOUNDS,
            )
            final_population = next_generation(
                ranked,
                pop_size=cfg.pop_size,
                birth_tick=0,
                elite_frac=cfg.elite_frac,
                fresh_frac=cfg.fresh_frac,
                mutation_rate=cfg.mutation_rate,
                rng=w.rng,
            )

    # Summary
    print("\n=== FINAL ===")
    for r in results:
        print(
            f"gen {r.generation}: alive={r.n_alive_at_end}/{cfg.pop_size} "
            f"deaths={r.deaths_recorded} extremes={r.extremes_recorded} "
            f"champ_fit={r.champion_fitness:.3f} champ_eq=${r.champion_equity:.2f} "
            f"wall={r.wall_seconds:.1f}s"
        )

    if args.render_html:
        from .viz.chart3d import render_reef_html
        path = render_reef_html(
            final_population,
            book_snapshots=list(w.book),
            symbol=cfg.symbol,
            include_book=True,
        )
        print(f"\nreef visualization written: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
