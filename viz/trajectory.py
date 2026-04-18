"""
Trajectory plots — per-creature 3D path in (tick, equity, z_pressure) space.

Produces plotly trace dicts WITHOUT importing plotly — chart3d.py handles
the import. This way the core library has no hard dependency on plotly;
unit tests can run without it.
"""
from __future__ import annotations


def creature_trajectory_traces(
    creatures,
    max_traces: int = 30,
    downsample_every: int = 1,
) -> list[dict]:
    """
    Build a list of plotly Scatter3d-compatible dicts, one per creature.

    Color:
      - alive at end  → 'rgba(0, 200, 120, 0.85)'  (green)
      - died          → 'rgba(220, 60, 60, 0.65)'  (red)

    X = tick, Y = equity, Z = z_pressure.
    """
    traces: list[dict] = []
    listed = list(creatures)
    # Prioritize alive creatures; cap at max_traces
    listed.sort(key=lambda c: (not c.alive, -(c.equity_peak or 0.0)))
    listed = listed[:max_traces]

    for c in listed:
        if not c.trajectory:
            continue
        step = max(1, int(downsample_every))
        pts = c.trajectory[::step]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        color = "rgba(0, 200, 120, 0.85)" if c.alive else "rgba(220, 60, 60, 0.65)"
        label = f"{c.id[:8]} {'alive' if c.alive else 'dead@t=' + str(c.death_tick)}"
        traces.append({
            "type": "scatter3d",
            "mode": "lines",
            "x": xs, "y": ys, "z": zs,
            "line": {"width": 2, "color": color},
            "name": label,
            "hovertemplate": (
                "tick=%{x}<br>equity=$%{y:.2f}<br>z=%{z:.3f}<extra>" + label + "</extra>"
            ),
        })
    return traces
