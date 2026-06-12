#!/usr/bin/env bash
set -euo pipefail
: "${HERMES_HOME:=$HOME/.hermes}"
python - <<'PY'
import sqlite3
import sqlite_vec
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)
print('sqlite-vec OK:', conn.execute('select vec_version()').fetchone()[0])
PY
mkdir -p "$HERMES_HOME/plugins"
cp -R "$(dirname "$0")/lifeengine" "$HERMES_HOME/plugins/lifeengine"
echo "Installed LifeEngine to $HERMES_HOME/plugins/lifeengine"
echo "Now run: hermes plugins enable lifeengine"
