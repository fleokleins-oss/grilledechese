"""
Chart3D — render the Reef as a single interactive HTML using plotly.

Layers:
  1. Creature trajectories (scatter3d lines, alive green, dead red)
  2. Book surface (optional — only shown if include_book=True)

The HTML is self-contained using plotly's CDN include mode, so it can be
opened offline-ish without Jupyter. We prefer plotly's `to_html` if the
library is available; otherwise we fall back to a minimal standalone HTML
that embeds plotly from CDN and constructs the figure client-side.
"""
from __future__ import annotations
import json
from pathlib import Path

from ..paths import VIZ_HTML_FILE
from .trajectory import creature_trajectory_traces
from .book_surface import book_surface_trace


def _layout(symbol: str = "reef") -> dict:
    return {
        "title": f"Encruzilhada3D — Reef — {symbol}",
        "scene": {
            "xaxis": {"title": "tick (time)"},
            "yaxis": {"title": "equity (USD)"},
            "zaxis": {"title": "Z pressure / depth"},
            "aspectmode": "manual",
            "aspectratio": {"x": 2.0, "y": 1.0, "z": 0.8},
        },
        "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
        "showlegend": True,
        "legend": {"itemsizing": "constant"},
        "paper_bgcolor": "#0b0b0c",
        "font": {"color": "#d0d0ce"},
    }


def render_reef_html(
    creatures,
    book_snapshots=None,
    output_path: Path | None = None,
    symbol: str = "reef",
    include_book: bool = True,
    max_traces: int = 30,
) -> Path:
    """
    Render the Reef visualization to an HTML file.

    Returns the path to the written HTML.
    """
    # Build traces
    traces: list[dict] = []
    traces.extend(creature_trajectory_traces(creatures, max_traces=max_traces))
    if include_book and book_snapshots:
        from ..market.surface import book_surface_z
        surf = book_surface_z(book_snapshots, levels=10, level_width_bps=2.0)
        bt = book_surface_trace(surf, opacity=0.25)
        if bt is not None:
            traces.append(bt)

    layout = _layout(symbol)
    out = Path(output_path) if output_path is not None else VIZ_HTML_FILE
    out.parent.mkdir(parents=True, exist_ok=True)

    # Path A: use plotly if available
    try:
        import plotly.graph_objects as go  # type: ignore
        fig = go.Figure(data=traces, layout=layout)
        fig.write_html(out, include_plotlyjs="cdn", full_html=True)
        return out
    except ImportError:
        pass

    # Path B: emit minimal standalone HTML that loads plotly from CDN
    html = _standalone_html(traces, layout, symbol=symbol)
    out.write_text(html, encoding="utf-8")
    return out


def _standalone_html(traces: list[dict], layout: dict, symbol: str) -> str:
    """Fallback HTML without plotly Python — loads plotly.js from CDN."""
    data_json = json.dumps(traces)
    layout_json = json.dumps(layout)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Encruzilhada3D — {symbol}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body {{ margin: 0; background: #0b0b0c; color: #d0d0ce; font-family: monospace; }}
  #reef {{ width: 100vw; height: 100vh; }}
</style>
</head>
<body>
<div id="reef"></div>
<script>
  const data = {data_json};
  const layout = {layout_json};
  Plotly.newPlot('reef', data, layout, {{responsive: true}});
</script>
</body>
</html>
"""
