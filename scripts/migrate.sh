#!/usr/bin/env bash
# Run Alembic migrations from the project root.
# Usage: bash scripts/migrate.sh [upgrade head | downgrade -1 | etc.]
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SRC="$REPO_ROOT/src"

# Find python: prefer project venv, fall back to system
if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python3"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
else
    PYTHON="$(which python3)"
fi

export PYTHONPATH="$SRC"
CMD="${@:-upgrade head}"
echo "Alembic: $CMD  (db: $REPO_ROOT/data/agent.db)"
cd "$REPO_ROOT" && "$PYTHON" -c "
import sys, os
sys.path.insert(0, '$SRC')
os.chdir('$REPO_ROOT')
from alembic.config import Config
from alembic import command as alembic_cmd
cfg = Config('$SRC/alembic.ini')
cfg.set_main_option('script_location', '$SRC/alembic')
args = '$CMD'.split()
getattr(alembic_cmd, args[0])(cfg, *args[1:])
"
