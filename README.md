# Encruzilhada3D / Reef

Tick-by-tick survival ecosystem for trading genomes. Selects for **survival** and
**convexity**, never for Sharpe in isolation.

Each creature is born with **$100 USD**, reads the book at each tick, decides, and
either survives, dies from ruin, or gets pushed into the tail bank graveyard
where it penalizes future genomes that resemble it.

---

## Philosophy (what the code refuses to do)

| Anti-pattern | Where it's prevented |
|---|---|
| **Replay bias** | `decide()` only sees features computed from `snapshot[t]`; `world3d` never passes future snapshots. |
| **False ecology** | Z-pressure is used as an entry gate, a size shrinker, AND a fill-price multiplier — it affects real decisions and real P&L. |
| **Tail underfitting** | Dead creatures' genomes are written to `tail_bank.jsonl`; new genomes are penalized by *distance* to those corpses. |
| **Fitness drift** | Returns are always in **decimal**. `log_growth_annual × regime × survival × (1+convexity) − tail_local`. No bps × bps. |
| **Latency blindness** | Slippage is a function of Z (spread + depth + vol + size). Taker fees applied once. Funding slot reserved. |
| **Signal correlation trap** | 4 orthogonal components (z-momentum, MR residual, range-expansion, OBI) combined by gene-selected mix. |
| **Double-counted fees** | Only `execution/fees.py` sets or charges fees. A unit test greps the repo to enforce this. |

---

## Architecture

```
encruzilhada3d/
├── creatures/       genes, creature lifecycle, actions (decide), fitness
├── execution/       fees (unique), slippage (Z), fills, simulator (step)
├── market/          book (parquet + synthetic), features (orthogonal), regimes, surface
├── engine/          tail_bank, selection, reproduction, world3d (main loop)
├── viz/             trajectory, book_surface, chart3d (HTML renderer)
├── tests/           test_reef_mvp.py (11 tests, runtime <1s)
├── systemd/         encruzilhada3d.service (user unit)
├── __main__.py      CLI entry point
├── paths.py         ENC3D_DATA_ROOT, ENC3D_STATE_ROOT
└── README.md
```

---

## The fitness formula

```
fitness = log_growth_annual × regime_factor × survival_factor × (1 + convexity_bonus)
        − tail_penalty_local
```

All quantities are **decimal** (never bps).

### log_growth_annual

```
log_growth_annual = ln(final_equity / initial_capital) × (365 / days_lived)
```

Dead creatures (final_equity ≤ 0) get `-10.0` (finite sentinel so selection can still
rank them among themselves).

### regime_factor

Soft penalty on regime specialists. `1.0` for `regime_preference == "any"`, `0.85` otherwise.
Future: replace with proper per-regime P&L attribution.

### survival_factor ∈ [0, 1]

```
raw = (ticks_alive / world_ticks) × (1 − max_drawdown)
if alive_at_end:
    raw *= 1.10
survival_factor = clip01(raw)
```

Long uptime × small drawdown × still-breathing = ~1.0. Short life or big DD = near 0.

### convexity_bonus ∈ [0, 0.20]

Taleb-style: reward positive return during vol-spike ticks (where `|ret| > 2σ`).

```
if ticks_in_vol_spike > 0:
    avg_spike_ret = sum_spike_ret / ticks_in_vol_spike
    if avg_spike_ret > 0:
        bonus = 0.20 × min(1, avg_spike_ret / 0.01)
```

### tail_penalty_local ∈ [0, 0.5]

Distance-based penalty. For each death event in the tail bank, compute
`normalized_distance(genome, event_genome)` using GENE_BOUNDS. Take the **top 5
closest events**; if their mean distance is below the threshold (0.15 by default),
penalty scales as `0.5 × (threshold − mean_d) / threshold`.

`0.0` when the tail bank has fewer than 10 death events (too sparse to trust).

---

## The Z-axis (execution pressure)

Z ∈ [0, 1] is computed per creature per tick:

```
Z = 0.30 × spread/REF_SPREAD
  + 0.30 × (1 − min(available_depth/desired_size, 1))
  + 0.20 × sigma/REF_SIGMA
  + 0.20 × desired_size/REF_SIZE_USD
```

Z enters the system in three places:

1. **Gate** — if `Z > gene.max_pressure_z`, creature skips entry (exits always allowed).
2. **Size shrink** — `size_fraction = kelly_cap × confidence × (1 − Z)`.
3. **Slippage** — `slip = Z² × 30bps`, applied to fill price: buy = ask × (1+slip), sell = bid × (1−slip).

Reference scales in `execution/slippage.py` are env-configurable.

---

## GENE_BOUNDS

| Family | Genes | Example |
|---|---|---|
| Perception | `lookback_ticks`, `spread_sensitivity`, `imbalance_threshold`, `depth_decay_lambda` | how it reads the book |
| Decision | `min_confidence`, `signal_mix` ∈ {momentum, mean_rev, breakout, adaptive} | when to act |
| Risk | `kelly_cap`, `stop_loss_pct`, `take_profit_pct`, `time_stop_ticks` | how big, when to stop |
| Execution | `max_pressure_z`, `cooldown_ticks` | how it responds to Z |
| Regime | `regime_preference` ∈ {trending, mean_rev, volatile, any} | when it trades |
| Survival | `ruin_threshold_pct` | when it dies |

---

## Usage

### One-shot run

```bash
# Tiny smoke (synthetic stream, no data needed)
python -m encruzilhada3d --synthetic --pop 30 --ticks 800 --gens 3 --render-html

# Real data (expects parquet under $ENC3D_DATA_ROOT/recorder_active/SYMBOL/)
export ENC3D_DATA_ROOT=~/apex_data
export ENC3D_STATE_ROOT=~/state/encruzilhada3d
python -m encruzilhada3d --symbol ADAUSDT --pop 80 --ticks 5000 --gens 5 --days 2.0 --render-html
```

Outputs land in `$ENC3D_STATE_ROOT`:

```
creatures.jsonl       one JSON per creature (final state of last generation)
tail_bank.jsonl       death_event + extreme_event records
champion.json         best creature of the last gen
world.log             per-gen timestamped log
reef.html             3D plotly visualization (if --render-html)
```

### Tests

```bash
cd /path/to/project_root   # where encruzilhada3d/ is a subdir
python -m unittest encruzilhada3d.tests.test_reef_mvp -v
```

Expected: `Ran 11 tests`, all pass, <2s wall.

### systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp encruzilhada3d/systemd/encruzilhada3d.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now encruzilhada3d.service
journalctl --user -u encruzilhada3d -f
```

The unit file sets `CPUQuota=80%`, `MemoryMax=2G`, `Nice=15` so the Reef cannot
starve a co-hosted live trading bot.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `ENC3D_DATA_ROOT` | `./apex_data` | where `recorder_active/SYMBOL/*.parquet` lives |
| `ENC3D_STATE_ROOT` | `./state/encruzilhada3d` | where state files are written |
| `ENC3D_FEE_BPS_TAKER` | `5.0` | taker fee in bps |
| `ENC3D_FEE_BPS_MAKER` | `1.0` | maker fee in bps |
| `ENC3D_FUNDING_BPS` | `0.0` | per-interval funding charge |
| `ENC3D_FUNDING_N_TICKS` | `0` | ticks between funding charges (0 = off) |
| `ENC3D_REF_SPREAD` | `0.0010` | normalization reference for spread in Z |
| `ENC3D_REF_SIGMA` | `0.005` | normalization reference for sigma in Z |
| `ENC3D_REF_SIZE_USD` | `1000.0` | normalization reference for order size in Z |

Z component weights (`ENC3D_W_SPREAD`, `ENC3D_W_DEPTH`, `ENC3D_W_VOL`, `ENC3D_W_SIZE`)
default to 0.30/0.30/0.20/0.20 and can be tuned per venue.

---

## Known limitations (MVP)

1. **Long-only** — shorting would invert Z sign and complicate convexity accounting.
   Not hard to add; intentionally deferred.
2. **No pyramiding** — one position at a time per creature. Simpler state, cleaner fitness.
3. **Instant fills** — `delay_ticks=0` everywhere. Slot reserved in `FillResult` for a
   latency model.
4. **regime_factor is flat** — specializers get 0.85, generalists 1.0. When per-regime
   P&L attribution is wired, replace with weighted cross-regime performance.
5. **Synthetic spikes are periodic** — deterministic every 250 ticks in the fallback
   stream. Good for unit tests, not for statistical robustness. Real data fixes this.

---

## Invariants (enforced by tests)

- **Fees only in `execution/`** — `test_no_other_module_references_fee_rates` greps
  the whole package.
- **Gene bounds respected under mutation** — 100 iterations of `mutate(rate=1.0)`.
- **Distance symmetric, zero for identical** — sanity on `normalized_distance`.
- **Tail penalty = 0 for empty bank**, positive at zero-distance.
- **Creature cash never negative** through buy→sell roundtrip.
- **World respires** — 10 creatures × 200 ticks × 1 gen produces all state files.
