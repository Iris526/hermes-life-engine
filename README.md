# LifeEngine for Hermes Agent

LifeEngine is a **Hermes Agent directory plugin** that gives an agent a traceable “life runtime”: canon/config, resources, schedule, events, sleep/reply/dream state, goals, memory, autonomy, review inbox, proactive intents, closet/collections, behavior mappings, and a small WebUI observatory.

Current release in this repository:

- Plugin version: `0.12.7`
- DB schema version: `42`
- Runtime storage: SQLite + `sqlite-vec`
- Integration model: standalone Hermes directory plugin; no Hermes core fork required

> This project is experimental. Treat it as a playground for agent-life UX, not as a medical, legal, financial, or safety-critical system.

## What you can do with it

LifeEngine is useful if you want a Hermes agent to have persistent, inspectable self-life state instead of only chat memory.

Examples:

- keep a Life Canon / setup draft for the agent’s identity, world rules, resources, and truth sources
- create schedules and events that can be completed, postponed, skipped, or reflected on
- model sleep, naps, delayed replies, dream/audit cycles, and call overrides
- track resources such as energy, focus, mood, fatigue, money-like custom resources, or inventory
- use a human-readable review inbox instead of raw JSON
- run a WebUI observatory at `http://127.0.0.1:8765`
- manage editable closet/collection systems for outfits and item assets
- map public narrative behaviors to private execution-only sources without exposing those sources in normal chat

## Requirements

- Linux/macOS/WSL shell
- Python environment used by Hermes
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) installed and working
- `sqlite-vec` importable in the same Python environment

Check Hermes first:

```bash
hermes doctor
hermes plugins list
```

If `sqlite-vec` is missing:

```bash
python -m pip install sqlite-vec
```

## Quick install from GitHub

```bash
git clone https://github.com/Iris526/hermes-life-engine.git
cd hermes-life-engine
python -m pip install -r requirements.txt
./install.sh
hermes plugins enable lifeengine
```

Then restart your Hermes session / gateway so the plugin tools and `/life` slash command are loaded.

Verify:

```bash
hermes plugins list --plain --no-bundled | grep lifeengine
hermes lifeengine doctor --level quick
hermes lifeengine status
```

Expected plugin version:

```text
0.12.7
```

## Upgrade an existing install

From a fresh clone or updated checkout:

```bash
cd hermes-life-engine
git pull
python -m pip install -r requirements.txt
./install.sh
hermes plugins enable lifeengine
hermes lifeengine doctor --level quick
```

The runtime migrates the LifeEngine database schema automatically. v0.12.7 expects schema `42`.

For cautious upgrades, back up your existing plugin and LifeEngine DB first:

```bash
cp -a ~/.hermes/plugins/lifeengine ~/.hermes/plugins/lifeengine.backup.$(date +%Y%m%d-%H%M%S)
hermes lifeengine backup
```

## Human command surface

Most people only need these:

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
/life living                  Concrete living layer helpers
/life closet                  Editable closet / collections
/life behavior                Public behavior → private source mappings
/life call                    Interrupt / wake / recover and reply
/life doctor                  Health check
/life backup                  Export backup
/life webui                   WebUI launch hint
/life advanced                Show advanced commands
```

CLI equivalents are under `hermes lifeengine ...`, for example:

```bash
hermes lifeengine doctor --level quick
hermes lifeengine schedule today
hermes lifeengine review
hermes lifeengine webui --open
```

## Agent tools provided

The plugin registers these Hermes tools:

```text
life_status, life_interface, life_living, life_collection, life_behavior,
life_upgrade, life_doctor, life_review, life_schedule, life_config,
life_control, life_setup, life_commit, life_resource, life_event,
life_sleep, life_dream, life_reply, life_call, life_memory, life_tick,
life_diary, life_trace, life_final_gate, life_truth, life_inventory,
life_confirmation, life_goal, life_autonomy, life_proactive,
life_execution, life_policy, life_webui
```

For most integrations, prefer the higher-level human surfaces and `life_interface` before reaching for low-level write tools.

## WebUI / Observatory

Start the local WebUI:

```bash
hermes lifeengine webui --open
```

Or point it at a specific LifeEngine directory / DB:

```bash
hermes lifeengine webui --life-dir ~/.hermes/lifeengine --open
hermes lifeengine webui --life-dir ~/.hermes/lifeengine/lifeengine.db --open
```

Default URL:

```text
http://127.0.0.1:8765
```

The WebUI is for local observability: state, schedules, review items, sleep/dream/reply surfaces, and related diagnostics.

## Closet / collections

v0.12.6 introduced editable collections; this repo also includes a local safety rule used by the current deployment:

- new items enter a collection first
- collections define image-generation / material / view rules
- using an item in checkout or outfit flows requires an available image asset with non-empty `asset_uri`
- if no usable image exists, LifeEngine lazily creates or reuses pending asset jobs
- outfit/use flows must not reconstruct clothing or props from text-only descriptions

Useful commands:

```bash
hermes lifeengine closet
hermes lifeengine closet init
hermes lifeengine closet add wardrobe 白色短上衣 轻薄棉混纺
hermes lifeengine closet outfit
```

Slash command equivalents:

```text
/life closet
/life closet init
/life closet wardrobe
/life closet outfit
```

Built-in editable defaults:

```text
wardrobe
shoe_cabinet
sock_drawer
accessory_cabinet
vanity
```

Advanced users can add, update, or archive collections such as `tool_cabinet`, `weapon_cabinet`, or project-specific inventories.

## Behavior mapping / private source routing

v0.12.7 adds behavior mappings. A public narrative action can map internally to private execution-only sources.

Example:

```text
Public phrase: 逛街买衣服
Internal sources: magazines, lookbooks, shops, inventory gaps, other references
```

The public phrase remains the user-facing story. Internal sources should not be exposed in ordinary narration.

Commands:

```bash
hermes lifeengine behavior
hermes lifeengine behavior init
hermes lifeengine behavior resolve 逛街买衣服
```

Slash command equivalents:

```text
/life behavior
/life behavior init
/life behavior resolve 逛街买衣服
```

Agent-facing route:

```json
{"action":"read","domain":"behavior","view":"summary"}
```

## Configuration and setup

LifeEngine starts from a Canon setup draft. Use:

```text
/life config
/life setup <your setting>
/life commit
```

Examples of settings you might add:

```text
/life setup timezone is Asia/Tokyo
/life setup default sleep is 23:30 to 07:00
/life setup weather can use narrative simulator when no real weather source is configured
/life commit
```

For agent self-life, narrative simulation can be allowed by Canon. For user-life facts, confirmations should be explicit.

## Development

Run tests in package layout:

```bash
python -m compileall -q .
rm -rf /tmp/lifeengine_pkgtest
mkdir -p /tmp/lifeengine_pkgtest/lifeengine
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ /tmp/lifeengine_pkgtest/lifeengine/
cd /tmp/lifeengine_pkgtest
PYTHONPATH=/tmp/lifeengine_pkgtest python -m pytest lifeengine/tests -q -o 'addopts='
```

Current verification for v0.12.7 in this repo passed:

```text
183 passed
```

## Docs

Design documents are in `docs/`, including:

- `docs/lifeengine_total_design_v0_12_7.md` — behavior mapping / private source routing
- `docs/lifeengine_total_design_v0_12_6.md` — editable closet / collection system
- earlier `docs/lifeengine_total_design_v*.md` files for historical design notes

## Safety notes

- LifeEngine creates durable state. Use `/life review`, `/life trace`, and `/life doctor` when debugging.
- User-life facts should require confirmation; agent self-life can follow its configured Canon policy.
- FinalGate-style checks are advisory by default in this distribution unless you configure them otherwise.
- Behavior mapping private sources are for planning; normal user-facing wording should keep the public behavior phrase.
- Public repository users should review the code and run it in their own Hermes profile before trusting it with valuable data.

## License

No explicit open-source license file is currently included. Until a license is added by the repository owner, treat the code as source-available for personal evaluation and ask before redistributing or re-licensing.
