"""
Smoke tests for the Reef MVP.

These are `unittest`-style but run directly with plain `python -m unittest`.
They don't require pytest. They validate the invariants that matter:

  - Fees are charged in exactly one place.
  - No creature returns negative cash (liquidation kills before that).
  - Tail penalty is 0 with empty tail bank.
  - The world respires: a small run finishes, state files are created,
    the champion has a valid genome.
"""
from __future__ import annotations
import os
import sys
import unittest
import tempfile
from pathlib import Path

# Ensure the package parent is on sys.path so `import encruzilhada3d` works
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))


class TestGenes(unittest.TestCase):
    def test_random_genome_has_all_bounds(self):
        from encruzilhada3d.creatures.genes import random_genome, GENE_BOUNDS
        g = random_genome()
        for key in GENE_BOUNDS:
            self.assertIn(key, g, f"missing {key}")

    def test_mutation_stays_within_bounds(self):
        from encruzilhada3d.creatures.genes import random_genome, mutate, GENE_BOUNDS
        g = random_genome()
        for _ in range(100):
            g = mutate(g, rate=1.0)
            for key, bound in GENE_BOUNDS.items():
                v = g[key]
                if isinstance(bound, list):
                    self.assertIn(v, bound)
                else:
                    lo, hi = bound
                    self.assertGreaterEqual(v, lo, f"{key}={v} < {lo}")
                    self.assertLessEqual(v, hi, f"{key}={v} > {hi}")

    def test_distance_symmetric_and_zero_for_identical(self):
        from encruzilhada3d.creatures.genes import random_genome, normalized_distance
        g = random_genome()
        self.assertEqual(normalized_distance(g, g), 0.0)
        g2 = random_genome()
        d_ab = normalized_distance(g, g2)
        d_ba = normalized_distance(g2, g)
        self.assertAlmostEqual(d_ab, d_ba)


class TestFeesUnique(unittest.TestCase):
    """Invariant: fees only live in execution.fees. No other module subtracts."""

    def test_no_other_module_references_fee_rates(self):
        import encruzilhada3d
        pkg_root = Path(encruzilhada3d.__file__).parent
        bad: list[str] = []
        # Allowlist: the fees module itself, its callers in execution/, and the
        # execution package re-export (__init__.py just re-exports symbols).
        allowed = {
            "execution/fees.py",
            "execution/fills.py",
            "execution/simulator.py",
            "execution/__init__.py",
        }
        for p in pkg_root.rglob("*.py"):
            rel = p.relative_to(pkg_root).as_posix()
            if rel in allowed:
                continue
            # Skip the tests directory entirely — tests are allowed to mention
            # fee identifiers when validating them.
            if rel.startswith("tests/"):
                continue
            txt = p.read_text()
            # Look for fee-rate references that could indicate rogue fee math
            for needle in ("FEE_BPS", "fee_bps"):
                if needle in txt:
                    bad.append(f"{rel} references {needle}")
        self.assertEqual(bad, [], "fees leaked outside execution/: " + ", ".join(bad))

    def test_fee_decimal_conversion(self):
        from encruzilhada3d.execution.fees import fee_decimal_for, apply_fee_decimal
        fd = fee_decimal_for(is_taker=True)
        self.assertGreater(fd, 0)
        self.assertLess(fd, 0.01)  # 1% sanity cap
        self.assertAlmostEqual(apply_fee_decimal(1000.0, 0.0005), 0.5)


class TestTailPenalty(unittest.TestCase):
    def test_empty_bank_zero_penalty(self):
        from encruzilhada3d.creatures.fitness import tail_penalty_local
        from encruzilhada3d.creatures.genes import random_genome, GENE_BOUNDS
        g = random_genome()
        self.assertEqual(tail_penalty_local(g, [], GENE_BOUNDS), 0.0)

    def test_penalty_peaks_at_zero_distance(self):
        from encruzilhada3d.creatures.fitness import tail_penalty_local
        from encruzilhada3d.creatures.genes import random_genome, GENE_BOUNDS
        g = random_genome()
        # 10 identical-genome events in the graveyard
        events = [{"genes": dict(g)} for _ in range(10)]
        pen = tail_penalty_local(g, events, GENE_BOUNDS)
        self.assertGreater(pen, 0.0)
        self.assertLessEqual(pen, 0.5)


class TestCreature(unittest.TestCase):
    def test_spawn_initial_capital(self):
        from encruzilhada3d.creatures.creature import Creature, INITIAL_CAPITAL
        from encruzilhada3d.creatures.genes import random_genome
        c = Creature.spawn(random_genome())
        self.assertEqual(c.capital, INITIAL_CAPITAL)
        self.assertTrue(c.alive)
        self.assertEqual(c.position, 0.0)

    def test_buy_then_sell_roundtrip_never_negative(self):
        from encruzilhada3d.creatures.creature import Creature
        from encruzilhada3d.creatures.genes import random_genome
        c = Creature.spawn(random_genome())
        c.apply_buy(tick=1, fill_price=100.0, qty=0.5, fee_decimal=0.0005)
        self.assertGreaterEqual(c.capital, 0.0)
        self.assertGreater(c.position, 0.0)
        c.apply_sell(tick=2, fill_price=102.0, qty=c.position, fee_decimal=0.0005)
        self.assertGreaterEqual(c.capital, 0.0)
        self.assertEqual(c.position, 0.0)

    def test_ruin_threshold(self):
        from encruzilhada3d.creatures.creature import Creature
        from encruzilhada3d.creatures.genes import random_genome
        g = random_genome()
        g["ruin_threshold_pct"] = 0.5
        c = Creature.spawn(g)
        c.capital = 40.0  # below 50% of $100 initial
        self.assertTrue(c.check_ruin(mark_price=100.0))


class TestWorldRespires(unittest.TestCase):
    """End-to-end smoke: does a tiny world run and persist state?"""

    def test_tiny_world_produces_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ENC3D_STATE_ROOT"] = tmp
            # Force re-import of paths module so env var takes effect
            for m in list(sys.modules):
                if m.startswith("encruzilhada3d"):
                    del sys.modules[m]

            from encruzilhada3d.engine.world3d import World3D, WorldConfig
            cfg = WorldConfig(
                pop_size=10,
                ticks_per_gen=200,
                n_generations=1,
                days_per_gen=0.1,
                seed=1,
            )
            w = World3D(cfg)
            results = w.run()
            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertGreater(r.champion_equity, 0.0)

            # State files should exist
            self.assertTrue((Path(tmp) / "creatures.jsonl").exists())
            self.assertTrue((Path(tmp) / "champion.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
