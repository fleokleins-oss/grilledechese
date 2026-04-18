"""
Book — market data ingestion.

Loads parquet L2/trades from ENC3D_DATA_ROOT, with a synthetic fallback for
tests and environments without real data. Exposes a BookStream that yields
per-tick snapshots of the form expected by features.py and fills.py.

Snapshot schema (dict):
  {
    "tick":               int,          # index in the stream
    "ts":                 float,         # epoch seconds
    "mid_price":          float,
    "best_bid":           float,
    "best_ask":           float,
    "bid_depth_usd":      float,         # top-of-book $ depth on bid side
    "ask_depth_usd":      float,
    "last_trade_qty":     float,         # most recent trade size (signed: +buy, -sell)
    "recent_returns":     list[float],   # window of decimal returns for feature compute
  }
"""
from __future__ import annotations
import math
import random
from pathlib import Path
from typing import Iterable, Iterator

from ..paths import DATA_ROOT


# -----------------------------------------------------------------------------
# Load parquet (graceful if pandas/pyarrow unavailable)
# -----------------------------------------------------------------------------
def _load_parquet_depth(symbol: str, max_rows: int = 20000):
    """Try to load depth parquet. Returns list[dict] or None."""
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        return None

    # Search a few common layouts: recorder_active/SYM/depth_YYYY-MM-DD.parquet
    # or recorder_legacy/sym/depth/YYYY.parquet
    candidates: list[Path] = []
    active_dir = DATA_ROOT / "recorder_active" / symbol.upper()
    if active_dir.exists():
        candidates.extend(sorted(active_dir.glob("depth_*.parquet")))
    legacy_dir = DATA_ROOT / "recorder_legacy" / symbol.lower() / "depth"
    if legacy_dir.exists():
        candidates.extend(sorted(legacy_dir.glob("*.parquet")))

    if not candidates:
        return None

    frames = []
    total = 0
    for p in candidates:
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        frames.append(df)
        total += len(df)
        if total >= max_rows:
            break
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).head(max_rows)
    return df


def load_symbol_data(symbol: str, max_rows: int = 20000):
    """
    Return a pandas DataFrame with the best-effort columns for the symbol.
    Falls back to None if no data available — callers can then use
    synthetic_book_stream() for a smoke run.
    """
    return _load_parquet_depth(symbol, max_rows=max_rows)


# -----------------------------------------------------------------------------
# BookStream abstraction
# -----------------------------------------------------------------------------
class BookStream:
    """Iterates per-tick book snapshots. Subclass or pass a list of dicts."""

    def __init__(self, snapshots: list[dict] | None = None):
        self._snapshots = list(snapshots) if snapshots else []

    def __len__(self) -> int:
        return len(self._snapshots)

    def __iter__(self) -> Iterator[dict]:
        return iter(self._snapshots)

    def at(self, tick: int) -> dict | None:
        if 0 <= tick < len(self._snapshots):
            return self._snapshots[tick]
        return None

    @classmethod
    def from_dataframe(
        cls,
        df,
        lookback: int = 100,
    ) -> "BookStream":
        """
        Build a BookStream from a pandas DataFrame with depth-like columns.
        Tries these column sets in order:
          - bid_px, ask_px, bid_qty, ask_qty, ts   (common recorder layout)
          - bid, ask                               (minimal fallback)
        Any missing column is synthesized conservatively.
        """
        try:
            import pandas as pd  # noqa: F401
        except ImportError:
            return cls([])

        cols = set(df.columns)
        snapshots: list[dict] = []
        recent_rets: list[float] = []
        last_mid = 0.0

        def _col(c: str, i: int, default: float) -> float:
            if c in cols:
                v = df[c].iloc[i]
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default
            return default

        n = len(df)
        for i in range(n):
            bid = _col("bid_px", i, _col("bid", i, 0.0))
            ask = _col("ask_px", i, _col("ask", i, 0.0))
            if bid <= 0 and ask > 0:
                bid = ask * 0.9999
            if ask <= 0 and bid > 0:
                ask = bid * 1.0001
            if bid <= 0 and ask <= 0:
                # unusable row — skip
                continue
            mid = (bid + ask) / 2.0

            bid_qty = _col("bid_qty", i, 1.0)
            ask_qty = _col("ask_qty", i, 1.0)
            bid_depth_usd = bid * bid_qty
            ask_depth_usd = ask * ask_qty

            ts = _col("ts", i, float(i))

            # Recent returns window (used by features)
            if last_mid > 0:
                r = (mid - last_mid) / last_mid
                recent_rets.append(r)
                if len(recent_rets) > lookback:
                    recent_rets = recent_rets[-lookback:]
            last_mid = mid

            snapshots.append({
                "tick": i,
                "ts": ts,
                "mid_price": mid,
                "best_bid": bid,
                "best_ask": ask,
                "bid_depth_usd": bid_depth_usd,
                "ask_depth_usd": ask_depth_usd,
                "last_trade_qty": 0.0,
                "recent_returns": list(recent_rets),
            })
        return cls(snapshots)


# -----------------------------------------------------------------------------
# Synthetic stream (fallback for tests / missing data)
# -----------------------------------------------------------------------------
def synthetic_book_stream(
    n_ticks: int = 2000,
    seed: int = 42,
    mu: float = 0.0,
    sigma: float = 0.001,
    spread_bps: float = 5.0,
    vol_spike_every: int = 250,
    vol_spike_sigma_mult: float = 8.0,
) -> BookStream:
    """
    Generate a synthetic BookStream with calm regime + periodic vol spikes
    (so convexity bonus has something to chew on).

    Returns a BookStream.
    """
    rng = random.Random(seed)
    price = 100.0
    snapshots: list[dict] = []
    recent_rets: list[float] = []
    for t in range(n_ticks):
        s = sigma
        if vol_spike_every > 0 and t > 0 and t % vol_spike_every == 0:
            s = sigma * vol_spike_sigma_mult
        r = rng.gauss(mu, s)
        price = max(0.01, price * (1.0 + r))

        spread_abs = price * (spread_bps * 1e-4)
        bid = price - spread_abs / 2.0
        ask = price + spread_abs / 2.0

        # Variable depth — thinner near vol spikes
        base_depth = 2000.0
        if s > sigma * 2:
            base_depth = base_depth / math.sqrt(s / sigma)
        bid_qty = base_depth / bid
        ask_qty = base_depth / ask

        recent_rets.append(r)
        if len(recent_rets) > 200:
            recent_rets = recent_rets[-200:]

        snapshots.append({
            "tick": t,
            "ts": float(t),
            "mid_price": price,
            "best_bid": bid,
            "best_ask": ask,
            "bid_depth_usd": bid * bid_qty,
            "ask_depth_usd": ask * ask_qty,
            "last_trade_qty": rng.choice([-1.0, 1.0]) * rng.uniform(0.01, 1.0),
            "recent_returns": list(recent_rets),
        })
    return BookStream(snapshots)
