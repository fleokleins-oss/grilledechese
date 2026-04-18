"""
Creature — the living unit of the Reef.

Each creature:
  - Is born with INITIAL_CAPITAL USD (default 100).
  - Holds a spot position in units of the asset (can be +long or 0; no short
    in the MVP to keep Z-pressure math clean — shorts would invert Z sign
    and we want convexity measured cleanly on the long side first).
  - Records its 3D trajectory: (tick, equity, z_pressure) per step.
  - Dies when equity drops below ruin threshold or when stopped out
    below liquidation.
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any


INITIAL_CAPITAL = 100.0  # USD


@dataclass
class Creature:
    # Identity
    id: str
    genes: dict
    birth_tick: int = 0
    parent_ids: tuple[str, ...] = field(default_factory=tuple)

    # Economic state
    capital: float = INITIAL_CAPITAL          # idle cash
    position: float = 0.0                      # units of asset held (long only)
    entry_price: float = 0.0                   # avg entry of current position
    entry_tick: int = -1                       # tick when position was opened

    # Survival state
    initial_capital: float = INITIAL_CAPITAL
    alive: bool = True
    death_tick: int = -1
    death_reason: str = ""                     # "ruin" | "liquidation" | "end"

    # Drawdown tracking (peak-to-trough of equity)
    equity_peak: float = INITIAL_CAPITAL
    max_drawdown: float = 0.0                  # worst observed drawdown fraction

    # Trajectory — list of (tick, equity, z_pressure). Downsample in viz.
    trajectory: list[tuple[int, float, float]] = field(default_factory=list)

    # Trade log — list of dicts with {tick, side, price, qty, notional, fee, pnl_decimal}
    trades: list[dict] = field(default_factory=list)

    # Execution counters (for convexity / regime factor later)
    ticks_alive: int = 0
    ticks_in_position: int = 0
    ticks_in_vol_spike: int = 0
    return_in_vol_spike: float = 0.0           # sum of decimal returns during |z|>2σ regime ticks

    # -------------------------------------------------------------------
    # Equity / marking
    # -------------------------------------------------------------------
    def equity(self, mark_price: float) -> float:
        """Mark-to-market total equity = cash + position * mark."""
        return self.capital + self.position * mark_price

    def unrealized_pnl_pct(self, mark_price: float) -> float:
        """Unrealized P&L as decimal return on entry notional. 0 if flat."""
        if self.position <= 0 or self.entry_price <= 0:
            return 0.0
        return (mark_price - self.entry_price) / self.entry_price

    # -------------------------------------------------------------------
    # Step recording (called every tick, before ruin check)
    # -------------------------------------------------------------------
    def record_step(
        self,
        tick: int,
        mark_price: float,
        z_pressure: float,
        is_vol_spike: bool = False,
        tick_return: float = 0.0,
    ) -> None:
        """Append to trajectory and update drawdown / convexity counters."""
        eq = self.equity(mark_price)
        if eq > self.equity_peak:
            self.equity_peak = eq
        dd = 0.0
        if self.equity_peak > 0:
            dd = (self.equity_peak - eq) / self.equity_peak
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        self.trajectory.append((int(tick), float(eq), float(z_pressure)))
        self.ticks_alive += 1
        if self.position > 0:
            self.ticks_in_position += 1
        if is_vol_spike:
            self.ticks_in_vol_spike += 1
            self.return_in_vol_spike += float(tick_return)

    # -------------------------------------------------------------------
    # Ruin check (called after record_step)
    # -------------------------------------------------------------------
    def check_ruin(self, mark_price: float) -> bool:
        """Return True if creature should die from ruin threshold breach."""
        eq = self.equity(mark_price)
        threshold_pct = float(self.genes.get("ruin_threshold_pct", 0.5))
        if eq <= self.initial_capital * threshold_pct:
            return True
        # Absolute safety: negative equity = liquidation
        if eq <= 0:
            return True
        return False

    def kill(self, tick: int, reason: str) -> None:
        self.alive = False
        self.death_tick = int(tick)
        self.death_reason = str(reason)

    # -------------------------------------------------------------------
    # Trade application — called by execution simulator
    # -------------------------------------------------------------------
    def apply_buy(
        self,
        tick: int,
        fill_price: float,
        qty: float,
        fee_decimal: float,
    ) -> None:
        """
        Apply a long entry. Cash decreases by (qty * fill_price + fee).
        Position and entry_price update as weighted average.
        """
        if qty <= 0 or fill_price <= 0:
            return
        notional = qty * fill_price
        fee_abs = notional * fee_decimal
        cost = notional + fee_abs
        if cost > self.capital:
            # Downsize to what we can afford (shouldn't happen if sizer works)
            qty = max(0.0, (self.capital - fee_abs) / fill_price)
            notional = qty * fill_price
            fee_abs = notional * fee_decimal
            cost = notional + fee_abs
            if qty <= 0:
                return

        # Weighted-average entry
        total_cost_prev = self.position * self.entry_price
        self.position += qty
        if self.position > 0:
            self.entry_price = (total_cost_prev + qty * fill_price) / self.position
        self.capital -= cost
        if self.entry_tick < 0:
            self.entry_tick = int(tick)

        self.trades.append({
            "tick": int(tick),
            "side": "buy",
            "price": float(fill_price),
            "qty": float(qty),
            "notional": float(notional),
            "fee_decimal": float(fee_decimal),
            "fee_abs": float(fee_abs),
            "pnl_decimal": 0.0,  # realized on exit only
        })

    def apply_sell(
        self,
        tick: int,
        fill_price: float,
        qty: float,
        fee_decimal: float,
    ) -> float:
        """
        Apply an exit (full or partial). Returns realized pnl_decimal for
        the portion sold. Cash increases by (qty * fill_price - fee).
        """
        if qty <= 0 or fill_price <= 0 or self.position <= 0:
            return 0.0
        qty = min(qty, self.position)
        notional = qty * fill_price
        fee_abs = notional * fee_decimal
        proceeds = notional - fee_abs

        # Realized PnL (decimal on the entry notional of the sold portion)
        pnl_decimal = 0.0
        if self.entry_price > 0:
            pnl_decimal = (fill_price - self.entry_price) / self.entry_price
            # Subtract fee impact on the return
            pnl_decimal -= fee_decimal

        self.capital += proceeds
        self.position -= qty
        if self.position <= 1e-12:
            self.position = 0.0
            self.entry_price = 0.0
            self.entry_tick = -1

        self.trades.append({
            "tick": int(tick),
            "side": "sell",
            "price": float(fill_price),
            "qty": float(qty),
            "notional": float(notional),
            "fee_decimal": float(fee_decimal),
            "fee_abs": float(fee_abs),
            "pnl_decimal": float(pnl_decimal),
        })
        return pnl_decimal

    # -------------------------------------------------------------------
    # Summary / serialization
    # -------------------------------------------------------------------
    def summary(self) -> dict:
        """Compact summary used by selection and for state files."""
        final_eq = self.trajectory[-1][1] if self.trajectory else self.capital
        realized = [t for t in self.trades if t["side"] == "sell"]
        wins = sum(1 for t in realized if t["pnl_decimal"] > 0)
        return {
            "id": self.id,
            "alive": self.alive,
            "birth_tick": self.birth_tick,
            "death_tick": self.death_tick,
            "death_reason": self.death_reason,
            "final_equity": float(final_eq),
            "initial_capital": float(self.initial_capital),
            "total_return_decimal": float((final_eq / self.initial_capital) - 1.0),
            "max_drawdown": float(self.max_drawdown),
            "ticks_alive": int(self.ticks_alive),
            "ticks_in_position": int(self.ticks_in_position),
            "ticks_in_vol_spike": int(self.ticks_in_vol_spike),
            "return_in_vol_spike": float(self.return_in_vol_spike),
            "n_trades": int(len(realized)),
            "win_rate": float(wins / len(realized)) if realized else 0.0,
            "genes": dict(self.genes),
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.summary(), sort_keys=True)

    # -------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------
    @classmethod
    def spawn(
        cls,
        genes: dict,
        birth_tick: int = 0,
        parent_ids: tuple[str, ...] = (),
        initial_capital: float = INITIAL_CAPITAL,
    ) -> "Creature":
        raw = f"{time.time_ns()}-{id(genes)}-{json.dumps(genes, sort_keys=True, default=str)}"
        cid = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return cls(
            id=cid,
            genes=dict(genes),
            birth_tick=int(birth_tick),
            parent_ids=tuple(parent_ids),
            capital=float(initial_capital),
            initial_capital=float(initial_capital),
            equity_peak=float(initial_capital),
        )
