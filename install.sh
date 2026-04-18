#!/usr/bin/env bash
# install.sh — Encruzilhada3D / Reef zero-friction installer.
#
# Extracts the package into the project root, validates syntax, runs tests,
# does a smoke run, renders HTML, and installs the systemd user service.
#
# Usage (from the directory where encruzilhada3d/ sits):
#   bash install.sh                                    # full install
#   bash install.sh --smoke-only                       # just validate + smoke
#   bash install.sh --no-systemd                       # skip systemd install
#
# Env vars it respects (all optional):
#   PROJECT_ROOT   where the enc3d package lives        (default: $PWD)
#   VENV_PATH      python venv to use                   (default: $HOME/mfv3_venv)
#   ENC3D_DATA_ROOT  L2 parquet dir                     (default: $HOME/apex_data)
#   ENC3D_STATE_ROOT write dir                          (default: $HOME/state/encruzilhada3d)

set -euo pipefail

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
log()  { echo -e "${G}▸${N} $*"; }
warn() { echo -e "${Y}⚠${N} $*"; }
die()  { echo -e "${R}✗${N} $*" >&2; exit 1; }

SMOKE_ONLY=0
NO_SYSTEMD=0
for arg in "$@"; do
    case "$arg" in
        --smoke-only) SMOKE_ONLY=1 ;;
        --no-systemd) NO_SYSTEMD=1 ;;
        *) warn "unknown arg: $arg" ;;
    esac
done

PROJECT_ROOT="${PROJECT_ROOT:-$PWD}"
VENV_PATH="${VENV_PATH:-$HOME/mfv3_venv}"
export ENC3D_DATA_ROOT="${ENC3D_DATA_ROOT:-$HOME/apex_data}"
export ENC3D_STATE_ROOT="${ENC3D_STATE_ROOT:-$HOME/state/encruzilhada3d}"

PY="python3"
if [ -x "$VENV_PATH/bin/python" ]; then
    PY="$VENV_PATH/bin/python"
    log "using venv python: $PY"
else
    warn "venv not found at $VENV_PATH — falling back to system python3"
fi

cd "$PROJECT_ROOT"
[ -d "encruzilhada3d" ] || die "encruzilhada3d/ not found in $PROJECT_ROOT"

# ─── 1. Syntax validation ────────────────────────────────────────────
log "validating syntax of all modules..."
$PY - << 'PYEOF'
import ast, pathlib, sys
errs = 0
for p in sorted(pathlib.Path("encruzilhada3d").rglob("*.py")):
    try:
        ast.parse(p.read_text())
    except SyntaxError as e:
        print(f"SYNTAX ERROR: {p}: {e}")
        errs += 1
if errs:
    sys.exit(1)
print(f"✓ {sum(1 for _ in pathlib.Path('encruzilhada3d').rglob('*.py'))} modules clean")
PYEOF

# ─── 2. Run tests ────────────────────────────────────────────────────
log "running test battery..."
ENC3D_STATE_ROOT="/tmp/enc3d_install_tests_$$" $PY -m unittest encruzilhada3d.tests.test_reef_mvp 2>&1 | tail -5
log "tests passed"

# ─── 3. Smoke run ────────────────────────────────────────────────────
log "smoke run: 20 creatures × 500 ticks × 2 gens (synthetic)..."
mkdir -p "$ENC3D_STATE_ROOT"
$PY -m encruzilhada3d \
    --synthetic \
    --pop 20 --ticks 500 --gens 2 --seed 42 \
    --render-html 2>&1 | tail -6

[ -f "$ENC3D_STATE_ROOT/reef.html" ] || die "reef.html not produced"
HTML_SIZE=$(stat -c%s "$ENC3D_STATE_ROOT/reef.html" 2>/dev/null || stat -f%z "$ENC3D_STATE_ROOT/reef.html")
log "reef.html: ${HTML_SIZE} bytes at $ENC3D_STATE_ROOT/reef.html"

if [ $SMOKE_ONLY -eq 1 ]; then
    log "smoke-only mode — stopping here"
    exit 0
fi

# ─── 4. systemd user service ─────────────────────────────────────────
if [ $NO_SYSTEMD -eq 1 ]; then
    warn "skipping systemd install (--no-systemd)"
else
    if ! command -v systemctl &>/dev/null; then
        warn "systemctl not found — skipping service install"
    else
        UNIT_DIR="$HOME/.config/systemd/user"
        mkdir -p "$UNIT_DIR"
        cp encruzilhada3d/systemd/encruzilhada3d.service "$UNIT_DIR/"
        systemctl --user daemon-reload
        log "systemd user unit installed at $UNIT_DIR/encruzilhada3d.service"
        echo ""
        echo "To enable and start:"
        echo "  systemctl --user enable --now encruzilhada3d.service"
        echo "  journalctl --user -u encruzilhada3d -f"
    fi
fi

echo ""
echo "═══════════════════════════════════════"
log "INSTALLED — the Reef is ready"
echo "═══════════════════════════════════════"
echo ""
echo "State dir:  $ENC3D_STATE_ROOT"
echo "Data dir:   $ENC3D_DATA_ROOT"
echo "Viz:        $ENC3D_STATE_ROOT/reef.html   (open in any browser)"
echo ""
echo "Common commands:"
echo "  $PY -m encruzilhada3d --help"
echo "  $PY -m encruzilhada3d --synthetic --render-html --pop 50 --ticks 2000 --gens 3"
echo "  $PY -m encruzilhada3d --symbol ADAUSDT --pop 80 --ticks 5000 --gens 5 --days 2.0 --render-html"
