# LifeEngine v0.12.5 — Complete Safe Interface + Living Rhythm Surface

v0.12.5 includes the v0.12.4 complete safe interface layer and adds the living rhythm layer that turns broad goals into concrete daily life.

## Product principle

Humans should see human-readable pages. Agents should get a complete, stable tool surface. Neither should write raw SQL.

## Event / Schedule semantics

- **Event** is the thing, intention, or life-object with a lifecycle.
- **ScheduleBlock** is the concrete reserved time window.
- `Event.status = planned` means planned but not necessarily scheduled.
- `ScheduleBlock.status = planned` means a concrete time block exists and is rendered as `已排期`.
- One Event can have many ScheduleBlocks because plans can be postponed, cancelled, or rescheduled.

## v0.12.4 interface additions

### `life_interface`

A unified safe routing tool for the Agent:

```json
{"action":"catalog"}
{"action":"read", "domain":"schedule", "view":"today"}
{"action":"write", "domain":"config", "intent":"patch", "text":"天气随机，时间和真实时间同步"}
{"action":"write", "domain":"schedule", "intent":"schedule_event", "event_id":"...", "start":"...", "end":"..."}
```

This is not a raw SQL interface. Reads route to domain APIs. Writes route to CanonDraft or LifeOps-backed methods.

### Expanded `life_config`

New actions:

- `requirements`: returns required setting specification.
- `suggest_defaults`: returns a draft patch suggestion.
- `apply_default_draft`: writes conservative defaults into CanonDraft only.

Default templates support both real-source and virtual-source worlds:

- `balanced`: real-time, weather from user-current-location by default.
- `virtual_random`: real-time, weather/environment from narrative simulator.

### Expanded `life_schedule`

Read actions:

- `today`
- `tomorrow`
- `week`
- `day`
- `unscheduled`
- `explain`

Write helpers:

- `schedule_event`
- `reschedule`
- `cancel`
- `complete`

These helpers still use LifeOps internally and do not write schedule tables directly.

## Human command surface

Ordinary humans still only need:

```text
/life
/life setup <设定>
/life commit
/life pause
/life resume
/life run
/life schedule [today|tomorrow|week|YYYY-MM-DD]
/life review
/life config
/life call
/life doctor
/life backup
/life advanced
```

Advanced users/agents can use:

```text
/life interface
/life interface read schedule today
/life interface write config patch 天气随机，时间同步
/life config requirements
/life config suggest_defaults
/life config apply_default_draft virtual_random
/life schedule unscheduled
/life schedule explain
```

## Safety invariants

1. No raw SQL.
2. Active Canon is never changed by `life_config patch`; changes go to CanonDraft.
3. Settings become active only after `/life commit`.
4. Schedule writes are LifeOps-backed.
5. User Life facts still require confirmation policies.
6. Agent self-life can be self-managed by autonomy and managed review.

---

# v0.12.5 — Living Rhythm / Canon Consistency / Concrete Self-Life

v0.12.5 responds to real Agent feedback: the runtime had enough structure, but autonomy could still produce abstract placeholders such as “推进目标：……”.  This release adds a living layer whose job is to turn broad goals into concrete daily life.

## New design principles

1. **Agent self-life is self-managed by default.** Safe self-maintenance, autonomy, and managed review are enabled for Agent-owned life. User Life remains confirmation-gated.
2. **No raw JSON for humans.** Human surfaces render timelines, lists, paper notes, and consistency reports. Raw details remain available behind advanced/debug tools.
3. **Abstract goals must become concrete life.** A broad daily-life goal should become events such as morning rounds, altar cleaning, inventory checks, low-risk commissions, bookkeeping, and pending notes.
4. **Canon consistency is a first-class doctor.** Required settings are not enough; timezones, currency, weather authority, resource keys, and stale markers must be checked together.
5. **Inventory and resource economy are part of life.** A temple-life agent should have talisman paper, incense, tools, clothes, tea, snacks, and a virtual currency economy.
6. **Proactive intent is a paper-note box.** Pending-only proactive is not an empty queue; it is a set of draft notes with why/tone/interrupt-risk/recommended handling.

## New capabilities

### Canon consistency doctor

`life_living(action="consistency")` checks:

- `truth_sources.time.value`, `truth_sources.time.timezone`, and `schedule_rules.timezone` mismatch.
- Currency binding vs `money.*` resources.
- Weather authority missing concrete location/fallback/rules.
- Stale delete markers or old tombstone-like values.

### Concrete day rhythm

`life_living(action="day_rhythm", preset="guimingguan")` creates concrete events and schedule blocks through LifeOps:

- 归明观晨巡与开观
- 打扫香案并补符纸
- 检查小型结界工具包
- 接一个低风险净符委托
- 傍晚记账与灵铢收支整理
- 写一张给 Ringo 的小纸条草稿

### Abstract goal event decomposition

`life_living(action="decompose_abstract")` detects broad goal placeholders and decomposes them into child events with schedules.

### Living inventory preset

`life_living(action="init_inventory")` defines a small living economy:

- `money.lingzhu`
- `daily_cost.lingzhu`
- `commission_income.lingzhu`
- `supplies.talisman_paper`
- `supplies.incense`
- `tools.barrier_meter_condition`
- `wardrobe.clean_outfits`

and initializes items such as 符纸、朱砂墨、香、小型结界仪、铜铃、归明观钥匙、委托记录册、干净道袍、茶叶、十二城小点心.

### Proactive paper notes

`life_living(action="paper_notes")` renders pending proactive intents as human-readable “小纸条”.

### Low-frequency diary draft

`life_living(action="diary_draft")` creates at most one internal daily diary draft unless forced.

## Human-facing command

```text
/life living
/life living consistency
/life living day
/life living inventory
/life living notes
/life living decompose [event_id]
```

Advanced Agent tool:

```json
{"action":"day_rhythm","preset":"guimingguan","date":"2030-01-02"}
```

## Schema

Schema version: 40.

New tables:

- `canon_consistency_reports`
- `life_rhythm_runs`
- `life_rhythm_items`
- `living_inventory_preset_runs`
