# Hermes LifeEngine Plugin

GitHub: <https://github.com/Iris526/hermes-life-engine>

LifeEngine is an optional Hermes plugin that gives an agent identity a persistent life state across sessions and platforms.

Current package contents:

- `plugin.yaml` — Hermes plugin metadata and provided tools/hooks.
- `*.py` — plugin runtime, LifeOps schemas, tools, hooks, migrations, maintenance, and validation.
- `skills/lifeengine/SKILL.md` — operational skill loaded by the plugin.
- `docs/design/life-engine-design.md` — design notes and product constraints.
- `docs/patches/` — historical FinalGate patch notes.

This repository intentionally excludes runtime state such as `lifeengine.db`, exports, backups, caches, and `__pycache__`.

## Install / sync into a Hermes profile

Copy this directory into a Hermes profile's plugin directory:

```bash
mkdir -p ~/.hermes/plugins
rsync -a --delete ./ ~/.hermes/plugins/lifeengine/
```

Then restart Hermes / gateway so plugin discovery reloads.

## Smoke checks

```bash
python -m compileall -q .
```
