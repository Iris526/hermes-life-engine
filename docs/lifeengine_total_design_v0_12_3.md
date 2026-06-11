# LifeEngine v0.12.3 — Event/Schedule Semantics and Canon IO Humanization

v0.12.3 fixes two product/design issues discovered while using the WebUI and human command surface:

1. **Event and Schedule needed clearer semantics.**  `Event.status = planned` does not mean the event has a real time slot.  A real schedule exists only when a `ScheduleBlock` with start/end is created and bound to the event.
2. **Settings/CANON read-write needed a friendly API.**  Agents need an easy way to read required settings, patch missing CanonDraft fields, and explain what is still missing, without using raw SQL or dumping JSON to humans.

## Event vs Schedule

LifeEngine now uses the following product semantics consistently in human surfaces:

- **Event** = the thing / intention / lifecycle, such as "buy a dress", "repair a node", "sleep", "study".
- **ScheduleBlock** = a concrete reserved time window for an event/action.
- One Event may have zero, one, or many ScheduleBlocks.
- Event `planned` means "计划中/待排期".
- Event `scheduled` means there is an active schedule block.
- ScheduleBlock `planned` is rendered to humans as "已排期" because it has actual start/end time.

## When does an event become scheduled?

An event is truly scheduled only when LifeEngine creates a `ScheduleBlock` with:

```text
event_id
start / end
start_ts / end_ts
status in planned/locked/ready/in_progress
```

This can happen via:

- user/Agent natural plan with concrete time followed by scheduler LifeOps,
- `life_event(action="schedule")`,
- Autonomy Planner creating a next step with a time block,
- daily sleep schedule generation,
- reschedule after postponement,
- explicit ScheduleBlock LifeOp.

## New schedule commands

Humans can now use:

```text
/life schedule
/life schedule tomorrow
/life schedule week
/life schedule 2026-06-11
/life schedule unscheduled
/life schedule explain
```

`unscheduled` lists planned events without an active schedule block.  `explain` prints the Event/Schedule relationship in human language.

## Canon / Settings IO

`life_config` now supports friendly read/write actions:

```text
summary/get/canon/show       Read active Canon in human terms.
check/missing/status         Required setting check.
draft                        Show active CanonDraft.
patch/set/write/update       Patch CanonDraft safely.
explain                      Explain how to read/write settings.
```

Patches never mutate active Canon directly.  They update CanonDraft only.  Activation still requires:

```text
/life commit
```

This keeps setup pollution-free while allowing the Agent to fill missing non-critical fields and ask the user about hard settings.

## Human command examples

```text
/life config
/life config check
/life config set truth_sources.bindings.weather.authority narrative_simulator
/life setup 你生活在一个虚拟城市，天气随机，但时间和真实时间同步。
/life commit
```

## Agent tool examples

```json
{
  "action": "summary"
}
```

```json
{
  "action": "patch",
  "path": "truth_sources.bindings.weather.authority",
  "value": "narrative_simulator"
}
```

```json
{
  "action": "patch",
  "text": "她生活在虚拟第七城，天气由叙事模拟器生成。"
}
```

## Design invariant

No component should make humans read raw JSON by default.  JSON remains available for debugging, but human surfaces should render timeline rows, setting summaries, review items, and clear status labels.

