"""
Book surface — a 3D representation of the order book evolution over time.

Axes:
  X = time (tick index)
  Y = price (absolute price level, relative to mid at the center)
  Z = depth (available USD at that price level)

The surface is sampled as a grid of (tick, price_level) → depth_usd, suitable
for plotly Surface / Mesh3d rendering.

In the MVP we only have top-of-book depth in the snapshot, so the "surface"
here is a 2-row ribbon: the bid ladder and the ask ladder at each tick.
When richer L2 data is plumbed through the BookStream, extend the columns
and this module will produce a full surface without changing its API.
"""
from __future__ import annotations
from typing import Iterable


def book_surface_z(
    snapshots: Iterable[dict],
    levels: int = 10,
    level_width_bps: float = 2.0,
) -> dict:
    """
    Produce arrays suitable for plotly Surface:

      {
        "tick":      list[int]           of length T,
        "price":     list[float]         of length L,
        "depth_usd": list[list[float]]   shape (T, L),
      }

    price is expressed as a SIGNED offset in bps from mid: e.g. with
    levels=10 and level_width_bps=2, you get levels at -18, -14, ..., -2
    (bids) and +2, +6, ..., +18 (asks).
    """
    snaps = list(snapshots)
    T = len(snaps)
    if T == 0:
        return {"tick": [], "price": [], "depth_usd": []}

    L = max(2, int(levels))
    if L % 2 != 0:
        L += 1  # ensure even

    # Price offsets in bps, symmetric around 0 (skip 0 itself)
    w = float(level_width_bps)
    half = L // 2
    offsets_bps: list[float] = []
    for k in range(half, 0, -1):
        offsets_bps.append(-w * (2 * k - 1))  # -17, -13, ..., -1 for w=2, half=5
    for k in range(1, half + 1):
        offsets_bps.append(w * (2 * k - 1))   # +1, +5, ...

    ticks: list[int] = []
    depth_grid: list[list[float]] = []
    for s in snaps:
        tick = int(s.get("tick", len(ticks)))
        mid = float(s.get("mid_price", 0.0))
        bid_depth = float(s.get("bid_depth_usd", 0.0))
        ask_depth = float(s.get("ask_depth_usd", 0.0))
        if mid <= 0:
            continue
        ticks.append(tick)
        row: list[float] = []
        for off in offsets_bps:
            if off < 0:
                # exponential decay of bid depth away from top-of-book
                decay = 2 ** (-abs(off) / w / 2)
                row.append(bid_depth * decay)
            else:
                decay = 2 ** (-abs(off) / w / 2)
                row.append(ask_depth * decay)
        depth_grid.append(row)

    return {
        "tick": ticks,
        "price": offsets_bps,   # price AXIS in bps from mid
        "depth_usd": depth_grid,
    }
