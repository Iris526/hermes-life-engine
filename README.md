# LifeEngine Hermes Plugin v0.12.5

LifeEngine is an embedded, SQLite/sqlite-vec based Agent life runtime for Hermes. It gives an Agent its own Life Canon, resources, schedule, events, sleep, dreams, realtime state, autonomy, proactive intents, review inbox, traceable life journal, and a WebUI observatory.

- Plugin version: `0.12.5`
- DB schema version: `40`
- sqlite-vec: required by LifeEngine runtime
- Integration: Hermes directory plugin; no core-loop fork

## Install

```bash
pip install -r requirements.txt
./install.sh
hermes plugins enable lifeengine
```

## Human-first command surface

Most humans only need these commands:

```text
/life                         Human status page
/life setup <setting>         Edit Life Canon setup draft
/life commit                  Commit setup draft
/life pause                   Pause LifeEngine mutations
/life resume                  Resume LifeEngine
/life run                     Manual heartbeat tick
/life schedule [period/date]  Human-readable timeline; default=today
/life review                  Human-readable review inbox
/life config                  Required setting checklist
/life call                    Always interrupt / wake / recover and reply
/life doctor                  Health check
/life backup                  Export backup
/life webui                   WebUI launch hint
/life living                  生活节律 / 小纸条 / inventory 预设
/life advanced                Show advanced commands
```

Complex `life_*` tools remain available to the Agent; humans do not need to memorize them.

## WebUI / Observatory

Start the local observatory:

```bash
hermes lifeengine webui --open
```

Select a LifeEngine directory or DB:

```bash
hermes lifeengine webui --life-dir ~/.hermes/lifeengine --open
hermes lifeengine webui --life-dir ~/.hermes/lifeengine/lifeengine.db --open
```

Default URL:

```text
http://127.0.0.1:8765
```

## New in v0.12.5

v0.12.5 adds the concrete living layer: Canon consistency doctor, temple-life day rhythm generation, abstract goal event decomposition, living inventory/resource presets, proactive paper notes, low-frequency diary drafts, and a more human-readable Review grouping. Agent self-life can now move from abstract “推进目标” placeholders toward concrete daily routines and small commissions.

1. `life_interface` provides one safe Agent-facing router for catalog/read/write across config, schedule, event, resource, inventory, sleep, dream, review, truth, and trace.
2. `life_config` now exposes required-setting specs, default suggestions, and draft-only default application.
3. `life_schedule` now supports read views plus safe schedule write helpers: schedule_event, reschedule, cancel, and complete.
4. Human surfaces remain small and readable: `/life schedule`, `/life review`, `/life config`, `/life interface`.
5. WebUI readability improvements from v0.12.2 and Event/Schedule semantics from v0.12.3 remain included.

## Design docs

The current design document is bundled in the zip:

```text
docs/lifeengine_total_design_v0_12_5.md
```
