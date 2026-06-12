# LifeEngine Hermes Plugin v0.12.7

LifeEngine is an embedded, SQLite/sqlite-vec based Agent life runtime for Hermes. It gives an Agent its own Life Canon, resources, schedule, events, sleep, dreams, realtime state, autonomy, proactive intents, review inbox, traceable life journal, and a WebUI observatory.

- Plugin version: `0.12.7`
- DB schema version: `42`
- sqlite-vec: required by LifeEngine runtime
- Integration: Hermes directory plugin; no core-loop fork


## Behavior Mapping / Private Truth Source Routing

LifeEngine supports behavior mappings: a public narrative behavior such as
`逛街买衣服` can be mapped to private execution-only information sources such
as fashion magazines, brand lookbooks, marketplace browsing, inventory gaps, or
internal records. These sources are never exposed in user-facing narration.

Human commands:

```text
/life behavior
/life behavior init
/life behavior resolve 逛街买衣服
/life behavior add_source --behavior-key shopping_clothes --source-type magazine --name 时尚期刊
```

Agent tool:

```json
{"action":"read","domain":"behavior","view":"summary"}
```

Important rule: the Agent may use the private execution plan internally, but in
conversation it must keep the public narrative label, e.g. “逛街买衣服”.

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

## New in v0.12.7

v0.12.7 adds the concrete living layer: Canon consistency doctor, Guimingguan-style day rhythm generation, abstract goal event decomposition, living inventory/resource presets, proactive paper notes, low-frequency diary drafts, and a more human-readable Review grouping. Agent self-life can now move from abstract “推进目标” placeholders toward concrete daily routines and small commissions.

1. `life_interface` provides one safe Agent-facing router for catalog/read/write across config, schedule, event, resource, inventory, sleep, dream, review, truth, and trace.
2. `life_config` now exposes required-setting specs, default suggestions, and draft-only default application.
3. `life_schedule` now supports read views plus safe schedule write helpers: schedule_event, reschedule, cancel, and complete.
4. Human surfaces remain small and readable: `/life schedule`, `/life review`, `/life config`, `/life interface`.
5. WebUI readability improvements from v0.12.2 and Event/Schedule semantics from v0.12.3 remain included.

## Design docs

The current design document is bundled in the zip:

```text
docs/lifeengine_total_design_v0_12_6.md
```

## New in v0.12.7

v0.12.7 adds the editable Closet / Collection system:

```text
/life closet
/life closet init
/life closet wardrobe
/life closet shoes
/life closet socks
/life closet accessories
/life closet vanity
/life closet add wardrobe 白色短上衣 轻薄棉混纺
/life closet outfit
```

Built-in collection presets are not fixed ontology. They are editable defaults:

```text
wardrobe
shoe_cabinet
sock_drawer
accessory_cabinet
vanity
```

The Agent or an advanced user can add/update/archive collections such as weapon_cabinet or tool_cabinet, and define intake image-generation rules, usage rules, and maintenance rules.

New items are not allowed to appear only in prose. They enter a collection first, get pending asset-generation jobs according to that collection's rules, and then can be selected by outfit/use flows.

The current design document is bundled as:

```text
docs/lifeengine_total_design_v0_12_6.md
```

## v0.12.7 Behavior Mapping

LifeEngine now supports private behavior mappings. Example: the Agent can map the public behavior "逛街买衣服" to hidden information sources such as fashion magazines, brand sites, and Taobao-like shops. These sources are planning references only and must not be exposed in ordinary user-facing speech. Use `/life behavior` or the `life_behavior` tool.

## v0.12.7 Behavior Mapping

Adds `life_behavior`: maps public narrative behaviors to private execution-only information sources. Example: `shopping_clothes` stays user-facing as “逛街买衣服” while internal sources may include magazines, brand websites, and marketplace browsing. These sources are hidden from user-facing narration.
