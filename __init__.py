"""
Encruzilhada3D / Reef — tick-by-tick survival ecosystem for trading genomes.

Core principles (no replay bias, no false ecology, no tail underfitting,
no fitness drift, no latency blindness, no signal correlation trap):

  - Each creature is born with 100 USD and decides per-tick with the book
    state visible at t (never t+1).
  - Execution pressure Z ∈ [0, 1] is computed from spread, depth decay, and
    creature position size — it affects real fill and fitness corrosion.
  - Fees are charged in exactly one place (execution/fees.py). No duplicates.
  - Tail penalty is LOCAL: distance from creature's genome to tail_bank events.
  - Fitness = log_growth_annual × regime_factor × survival_factor − tail_local.
  - Selection favors survival + convexity, never Sharpe isolated.

Paths are configurable via:
  - ENC3D_DATA_ROOT   (where L2/parquet data lives)
  - ENC3D_STATE_ROOT  (where creatures.jsonl, tail_bank.jsonl, champion.json live)
"""
__version__ = "0.1.0"
