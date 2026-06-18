# LifeEngine Hermes Plugin v0.17.0

LifeEngine is an embedded, SQLite/sqlite-vec based Agent life runtime for Hermes.

This release builds the **经营 (venture) system** on top of v0.16.0's recurring activities — an engine-enforced way to run businesses to earn a living: supply chain (进销存) so goods don't appear from nowhere (sell consumes stock, low stock auto-orders a 进货 event), opportunity triggers (委托/客人 arrive on their own per an arrival rate), no double-booking (一人不能分身), and operation models (active occupies her time; self_service is passive; staffed pays wages) with fixed/flexible location. Builds on v0.15.0's resource-model reshape and v0.16.0's recurring activities.

- Plugin version: `0.17.0`
- sqlite-vec: required by LifeEngine runtime
- Integration: Hermes directory plugin; no core-loop fork

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
/life advanced                Show advanced commands
```

Complex `life_*` tools remain available to the Agent.

## Design docs

The current design document is bundled here:

```text
docs/lifeengine_total_design_v0_14_0.md
```
