"""
Features — per-tick feature dictionary consumed by actions.py and slippage.py.

Keeps the 4 signal components ORTHOGONAL (no signal correlation trap):
  - ret_recent, ret_sigma     (z-momentum)
  - mean_rev_residual         (microprice vs SMA)
  - range_expansion_z         (window high/low expansion)
  - order_book_imbalance      (OBI from top-of-book depth)

Plus the execution-side features used by fills.simulate_fill and slippage:
  - spread_decimal, best_bid, best_ask, mid_price
  - available_depth_usd       (depth on the side a BUY would hit = ask side)
  - vol_regime                (soft regime indicator in [0, 1])
  - is_vol_spike              (bool — this tick's |return| > 2σ)
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class TickFeatures:
    mid_price: float
    best_bid: float
    best_ask: float
    spread_decimal: float
    ret_recent: float
    ret_sigma: float
    mean_rev_residual: float
    range_expansion_z: float
    order_book_imbalance: float
    available_depth_usd: float
    vol_regime: float
    is_vol_spike: bool

    def as_dict(self) -> dict:
        return {
            "mid_price": self.mid_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread_decimal": self.spread_decimal,
            "ret_recent": self.ret_recent,
            "ret_sigma": self.ret_sigma,
            "mean_rev_residual": self.mean_rev_residual,
            "range_expansion_z": self.range_expansion_z,
            "order_book_imbalance": self.order_book_imbalance,
            "available_depth_usd": self.available_depth_usd,
            "vol_regime": self.vol_regime,
            "is_vol_spike": self.is_vol_spike,
        }


def _sigma(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(max(0.0, var))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def compute_features(
    snapshot: dict,
    side_for_depth: str = "ask",
    vol_window: int = 50,
    range_window: int = 20,
) -> dict:
    """
    Compute the feature dict from a book snapshot.

    Args:
      snapshot:         dict from BookStream
      side_for_depth:   "ask" (buy side) or "bid" (sell side). The creature
                        is long-only in the MVP, so for entries we want
                        ask-side depth.
      vol_window:       how many recent returns to use for σ and regime
      range_window:     how many recent mid-prices to consider for range-expansion
    """
    mid = float(snapshot.get("mid_price", 0.0))
    bid = float(snapshot.get("best_bid", mid))
    ask = float(snapshot.get("best_ask", mid))
    spread_decimal = 0.0
    if mid > 0:
        spread_decimal = max(0.0, (ask - bid) / mid)

    recent = list(snapshot.get("recent_returns", []) or [])
    window = recent[-vol_window:] if recent else []

    ret_recent = window[-1] if window else 0.0
    sigma = _sigma(window)
    ret_sigma = sigma if sigma > 0 else 1e-8

    # Mean-reversion residual: current mid vs SMA(mid) — we don't carry a
    # mid series in the snapshot, so we approximate using cumulative recent
    # returns as (price / price_sma - 1).
    if len(window) >= 5:
        # Reconstruct relative path from window
        path = [1.0]
        for r in window:
            path.append(path[-1] * (1.0 + r))
        sma = _mean(path)
        last = path[-1]
        mean_rev_residual = (last - sma) / sma if sma > 0 else 0.0
    else:
        mean_rev_residual = 0.0

    # Range expansion z — compare |current return| to window σ
    if ret_sigma > 0:
        range_expansion_z = max(-3.0, min(3.0, ret_recent / ret_sigma))
    else:
        range_expansion_z = 0.0

    # OBI from top-of-book depth (in USD)
    bid_depth = float(snapshot.get("bid_depth_usd", 0.0))
    ask_depth = float(snapshot.get("ask_depth_usd", 0.0))
    total = bid_depth + ask_depth
    obi = 0.0 if total <= 0 else (bid_depth - ask_depth) / total
    obi = max(-1.0, min(1.0, obi))

    # Available depth on the relevant side (for Z-pressure size term)
    if side_for_depth == "ask":
        available_depth_usd = ask_depth
    else:
        available_depth_usd = bid_depth

    # Vol regime in [0, 1] — σ relative to a reference σ
    # Reference = 0.5% per-window σ (matches execution.slippage.REF_SIGMA)
    ref_sigma = 0.005
    vol_regime = min(1.0, (ret_sigma / ref_sigma)) if ref_sigma > 0 else 0.5

    # Vol spike: |return| > 2σ of the window
    is_vol_spike = abs(ret_recent) > 2.0 * sigma if sigma > 0 else False

    return TickFeatures(
        mid_price=mid,
        best_bid=bid,
        best_ask=ask,
        spread_decimal=spread_decimal,
        ret_recent=ret_recent,
        ret_sigma=ret_sigma,
        mean_rev_residual=mean_rev_residual,
        range_expansion_z=range_expansion_z,
        order_book_imbalance=obi,
        available_depth_usd=available_depth_usd,
        vol_regime=vol_regime,
        is_vol_spike=is_vol_spike,
    ).as_dict()
