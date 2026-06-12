#!/usr/bin/env bash
set -euo pipefail

: "${HERMES_HOME:=$HOME/.hermes}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HERMES_HOME/plugins/lifeengine"

python - <<'PY'
import sqlite3
try:
    import sqlite_vec
except Exception as exc:
    raise SystemExit(f"sqlite-vec is required. Install with: python -m pip install sqlite-vec\nOriginal error: {exc}")
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)
print('sqlite-vec OK:', conn.execute('select vec_version()').fetchone()[0])
PY

mkdir -p "$HERMES_HOME/plugins"
rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"
rsync -a \
  --exclude '.git' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$SRC_DIR/" "$DEST_DIR/"

echo "Installed LifeEngine to $DEST_DIR"
echo "Now run: hermes plugins enable lifeengine"
