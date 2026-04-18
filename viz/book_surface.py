"""
Book surface — plotly Surface-compatible trace dict.

Consumes the output of market.surface.book_surface_z and produces a trace
dict that chart3d can render. No hard plotly dependency.
"""
from __future__ import annotations


def book_surface_trace(surface_data: dict, opacity: float = 0.30) -> dict | None:
    """
    Convert surface dict {tick, price, depth_usd} to a plotly Surface trace.

    Returns None if the surface has no data.
    """
    ticks = surface_data.get("tick", [])
    prices = surface_data.get("price", [])
    depth = surface_data.get("depth_usd", [])
    if not ticks or not prices or not depth:
        return None

    # Plotly surface expects z shape (len(y), len(x)). We have shape
    # (len(ticks), len(prices)) = (T, L). For x=tick, y=price we transpose.
    T = len(ticks)
    L = len(prices)
    if len(depth) != T or any(len(row) != L for row in depth):
        return None

    z_t: list[list[float]] = [[0.0] * T for _ in range(L)]
    for i in range(T):
        for j in range(L):
            z_t[j][i] = float(depth[i][j])

    return {
        "type": "surface",
        "x": list(ticks),
        "y": list(prices),
        "z": z_t,
        "opacity": float(opacity),
        "showscale": False,
        "colorscale": "Viridis",
        "name": "book depth (Z=USD)",
        "hovertemplate": (
            "tick=%{x}<br>price_bps=%{y:.1f}<br>depth=$%{z:.0f}<extra>book</extra>"
        ),
    }
