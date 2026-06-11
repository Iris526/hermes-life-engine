# Hermes LifeEngine Plugin

GitHub: <https://github.com/Iris526/hermes-life-engine>

LifeEngine is an optional Hermes directory plugin that gives an agent identity a persistent, auditable life runtime across sessions and platforms.

Current version: `0.11.18`

Target schema: `38`

Required dependency: `sqlite-vec`

Integration model: Hermes directory plugin; no core-loop fork.

## What v0.11.18 contains

v0.11.18 updates the v0.10.0 plugin line with:

- Event V2 state fields and transition tables.
- SleepPlan / SleepSession, sleep debt, all-nighter, and recovery planning.
- ReplyGate, delayed replies, and `/life call` wake/interrupt flow.
- DreamRun / DreamAudit / DreamEntry and dream repair policy.
- Sleep-aware autonomy and execution simulation.
- Human `/life review` UX with preview/apply, batch apply, undo, managed loop, observability, and release-readiness reports.
- Policy UX for sleep / reply / dream presets, conflicts, export/import, and acceptance checks.

## Repository contents

- `plugin.yaml` — Hermes plugin metadata and provided tools/hooks.
- `*.py` — plugin runtime, LifeOps schemas, tools, hooks, migrations, review/sleep/dream/reply modules, maintenance, and validation.
- `skills/lifeengine/SKILL.md` — operational skill loaded by the plugin.
- `requirements.txt` — plugin Python dependency list.
- `docs/design/` — LifeEngine design notes and v0.10/v0.11 design references.
- `docs/patches/` — historical FinalGate patch notes.
- `docs/upgrade/0.11.18/` — upstream patch-package manuals, checklist, manifest, and checksums.

This repository intentionally excludes runtime state such as `lifeengine.db`, exports, backups, caches, and `__pycache__`.

## Install / sync into a Hermes profile

Install dependencies if needed:

```bash
pip install -r requirements.txt
```

Copy this directory into a Hermes profile's plugin directory:

```bash
mkdir -p ~/.hermes/plugins
rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  ./ ~/.hermes/plugins/lifeengine/
```

Then enable and restart Hermes / gateway so plugin discovery reloads:

```bash
hermes plugins enable lifeengine
```

## Upgrade checks

```bash
hermes lifeengine doctor
hermes lifeengine upgrade check --include-details
hermes lifeengine trace verify
```

Recommended additional checks:

```bash
hermes lifeengine upgrade verify_memory
hermes lifeengine upgrade package_check
hermes lifeengine review managed_observability
hermes lifeengine review managed_readiness
```

## Smoke checks for this source tree

```bash
python -m compileall -q .
python - <<'PY'
import importlib.util, pathlib, sys
root = pathlib.Path('.').resolve()
spec = importlib.util.spec_from_file_location('lifeengine', root / '__init__.py', submodule_search_locations=[str(root)])
mod = importlib.util.module_from_spec(spec)
sys.modules['lifeengine'] = mod
spec.loader.exec_module(mod)
print('LifeEngine import OK')
PY
```
