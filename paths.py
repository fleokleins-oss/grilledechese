"""Paths — single source of truth. All modules import from here."""
from __future__ import annotations
import os
from pathlib import Path

DATA_ROOT = Path(os.getenv("ENC3D_DATA_ROOT", "./apex_data")).resolve()
STATE_ROOT = Path(os.getenv("ENC3D_STATE_ROOT", "./state/encruzilhada3d")).resolve()

STATE_ROOT.mkdir(parents=True, exist_ok=True)

CREATURES_FILE = STATE_ROOT / "creatures.jsonl"
TAIL_BANK_FILE = STATE_ROOT / "tail_bank.jsonl"
CHAMPION_FILE = STATE_ROOT / "champion.json"
WORLD_LOG_FILE = STATE_ROOT / "world.log"
VIZ_HTML_FILE = STATE_ROOT / "reef.html"
