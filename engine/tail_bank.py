"""
Tail Bank — graveyard of extreme events and dead genomes.

Two kinds of records are stored in tail_bank.jsonl (one JSON per line):

  1. death_event:  when a creature dies (ruin / liquidation). Stores the
                   genome, the tick, the reason, and the market context.

  2. extreme_event: when the market itself prints a tail move (|return| > k·σ).
                    Stores the tick, return, σ, regime — no genome. Used by
                    convexity scoring to identify vol-spike ticks.

Local tail penalty reads death_event records by genome similarity
(see creatures/fitness.py::tail_penalty_local).
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Iterable

from ..paths import TAIL_BANK_FILE


class TailBank:
    def __init__(self, path: Path | None = None, max_keep: int = 5000):
        self.path = Path(path) if path is not None else TAIL_BANK_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_keep = int(max_keep)
        self._cache: list[dict] | None = None

    # -------------------------------------------------------------------
    # Writes
    # -------------------------------------------------------------------
    def record_death(self, creature, reason: str, context: dict | None = None) -> None:
        ev = {
            "kind": "death_event",
            "ts": time.time(),
            "tick": int(creature.death_tick),
            "creature_id": creature.id,
            "reason": str(reason),
            "final_equity": float(
                creature.trajectory[-1][1] if creature.trajectory else creature.capital
            ),
            "initial_capital": float(creature.initial_capital),
            "max_drawdown": float(creature.max_drawdown),
            "ticks_alive": int(creature.ticks_alive),
            "genes": dict(creature.genes),
            "context": dict(context or {}),
        }
        self._append(ev)

    def record_extreme(
        self,
        tick: int,
        ret: float,
        sigma: float,
        regime: str = "",
        k_sigma: float = 2.0,
    ) -> None:
        ev = {
            "kind": "extreme_event",
            "ts": time.time(),
            "tick": int(tick),
            "ret": float(ret),
            "sigma": float(sigma),
            "k_sigma": float(k_sigma),
            "regime": str(regime),
        }
        self._append(ev)

    def _append(self, ev: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, sort_keys=True, default=str) + "\n")
        self._cache = None  # invalidate
        self._trim_if_huge()

    def _trim_if_huge(self) -> None:
        """Keep the last `max_keep` lines to avoid unbounded growth."""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        if len(lines) <= self.max_keep:
            return
        lines = lines[-self.max_keep:]
        with self.path.open("w", encoding="utf-8") as f:
            f.writelines(lines)

    # -------------------------------------------------------------------
    # Reads
    # -------------------------------------------------------------------
    def load_all(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            return self._cache
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self._cache = out
        return out

    def deaths(self) -> list[dict]:
        return [e for e in self.load_all() if e.get("kind") == "death_event"]

    def extremes(self) -> list[dict]:
        return [e for e in self.load_all() if e.get("kind") == "extreme_event"]

    def extreme_tick_set(self) -> set[int]:
        return {int(e["tick"]) for e in self.extremes() if "tick" in e}

    def __iter__(self) -> Iterable[dict]:
        return iter(self.load_all())
