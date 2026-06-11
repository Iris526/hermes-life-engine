# LifeEngine v0.12.4 — Complete Safe Interface Surface

v0.12.4 completes the interface layer that sits between humans/agents and the LifeEngine database.

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
