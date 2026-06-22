# LifeEngine Hermes Plugin v0.18.0

LifeEngine is an embedded, SQLite/sqlite-vec based Agent life runtime for Hermes.

This release adds the **生成式内心生活 (LifeAuthor) layer** on top of the deterministic life substrate: authored dreams, companion follow-ups, cross-week campaigns, and reflection-driven opinions/self-narrative. It also includes a Social World slot layer for world-specific entities, affiliations, reputation, evaluations, and rumors. Resource accounting, schedule execution, sleep, venture/recurring activity settlement, review, trace, and receipts remain deterministic and auditable.

- Plugin version: `0.18.0`
- sqlite-vec: required by LifeEngine runtime
- Integration: Hermes directory plugin; no core-loop fork
- Authoring boundary: host-model calls are prepared outside SQLite write transactions; LifeOps only commits already-authored content or deterministic fallbacks.
- Social World boundary: LifeEngine stores generic social slots and ledgers; concrete worldviews define what each entity kind, reputation axis, evaluation axis, and rumor channel means.

## Game UI / Observatory

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

The WebUI now provides:

- Agent stage as the main visual
- pixel avatar state animation
- RPG-style status bars
- schedule as quest log
- bag/collections board
- closet and outfit items
- dreams and review inbox
- Hermes workspace markdown library
- trace/debug drawer

## Human command surface

Most humans only need:

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
/life living                  Concrete living rhythm / notes / resource preset
/life closet                  Collections / outfit / closet tools
/life behavior                Private behavior mapping
/life context                 Prompt/context slimming policy
/life relationship            Relationship memory for what the user shared
/life campaign                Cross-week themed arcs
/life opinion                 Evolving opinions and self-narrative
/life advanced                Show advanced commands
```

Complex `life_*` tools remain available to the Agent, including `life_social` for worldview social slots.

## Design docs

The current design document is bundled here:

```text
docs/lifeengine_total_design_v0_18_0.md
```
