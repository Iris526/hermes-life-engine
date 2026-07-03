# LifeEngine Total Design v0.6

## 1. Definition

LifeEngine is an embedded Agent-life runtime for Hermes and future Agent Loop
frameworks. It gives an Agent a persistent, inspectable, resource-bounded,
time-progressing, self-narrative life. The model may imagine and express, but
LifeEngine decides what becomes durable life state.

Core rule:

```text
No committed life state, no life claim.
```

Final answers must be supported by either existing canonical state or current
turn CommitReceipt facts.

## 2. Principles

1. **Canon First**: Life Canon is the highest Agent-life truth source below
   platform/runtime policy.
2. **Pause Means No Mutation**: setup/paused/read_only/disabled states prevent
   life pollution.
3. **LifeOps Only**: all durable mutations go through LifeOps, validation,
   SQLite transaction, CommitReceipt, Journal, and Trace.
4. **Resource Conservation**: scalar and entity resources must be registered and
   mutated through ledgers/movements.
5. **Heartbeat Progression**: Agent life can be advanced by heartbeat/wake jobs,
   not only by user questions.
6. **TruthSource Traceability**: external facts like weather are resolved,
   cached, and traced according to Canon bindings.
7. **User Life Safety**: user-side life uses the same schema but different truth
   policy; unconfirmed user facts are pending confirmations.
8. **Autobiographical Continuity**: goals, life arcs, reflections, diary,
   inventory, meals, resources, events, and memories all form the Agent's
   durable self-history.

## 3. Architecture

```text
LifeEngine
  Control Plane
    EngineState, ModuleGates, SetupMode, Pause/Resume, Workspace
  Canon Layer
    CanonDraft, CanonVersion, TruthSource bindings, Migration
  Mutation Layer
    LifeOps, Validator, Transaction, CommitReceipt, FinalGate
  Time Layer
    ScheduleBlock, WakeJob, Heartbeat, idempotency
  Resource Layer
    Scalar Resource, Resource Ledger, Reservations, Inventory, Meals
  Behavior Layer
    Event, Action, Result, Goals, Life Arcs, Decomposition, Reflection
  Memory Layer
    Structured Memory, FTS5, sqlite-vec, Diary, Reflection Memory
  Truth Layer
    Resolve, Observe, Cache, Trace
  Proactive Layer
    Intent, Outbox, pending-only default
  Trace Layer
    TraceRun, TraceSpan, LifeJournal, Hash Chain, Audit, Explain
  Adapter Layer
    Hermes tools/hooks/slash/CLI/cron; future framework adapters
```

## 4. Control states

```text
uninitialized -> setup_required -> setup -> active
active -> paused / paused_setup / read_only / disabled / archived
```

When in `setup` or `paused_setup`, natural language settings are written to
CanonDraft only. Life events, resource deltas, diary entries, memories, and
heartbeat actions are blocked.

## 5. Workspaces

```text
agent_self: Agent narrative life. Can use Canon-authorized narrative reality.
user_life: User real life. Cannot be invented; needs confirmation/import/report.
relationship: Agent-user relationship memory, isolated per user.
```

The same tables support each owner kind, but policy differs.

## 6. LifeOps and CommitReceipt

Every durable change is represented as a LifeOp:

```text
CREATE_EVENT
CREATE_SCHEDULE_BLOCK
COMPLETE_EVENT
RESOURCE_DEFINE / RESOURCE_DELTA / RESOURCE_RESERVE
CREATE_INVENTORY_ITEM / INVENTORY_DELTA / CREATE_MEAL_RECORD
CREATE_MEMORY / CREATE_DIARY
CREATE_GOAL / CREATE_LIFE_ARC / DECOMPOSE_EVENT / CREATE_REFLECTION
CREATE_PROACTIVE_INTENT
```

A committed transaction produces CommitReceipt facts such as:

```text
goal 准备七月考试 status=active progress=0
event event_xxx decomposed into 3 child events
inventory item 藏青色百褶裙 category=clothing quantity=1
```

FinalGate validates final response claims against receipt and canonical facts.

## 7. Goals, Life Arcs, and Decomposition

v0.6 adds the long-horizon layer:

- **Goal**: durable objective, e.g. prepare for an exam, build a fitness habit,
  save money, finish a creative project.
- **Life Arc**: narrative trajectory grouping goals/events, e.g. “exam prep
  season” or “learning to live more independently.”
- **Goal/Event Link**: an event contributes progress to a goal.
- **Event Decomposition**: a parent event can be split into child events and
  dependencies. Child events remain normal LifeEvents with status, schedule,
  resources, result, memory, and trace.
- **Milestone**: a target inside a goal.
- **Reflection**: review of event/goal/arc/day. Can create reflection memory.

Example:

```text
Goal: pass July exam
Arc: exam preparation life arc
Parent Event: prepare for July exam
Children:
  - buy textbook
  - review chapter 1
  - complete mock test
```

Completing child events can apply goal progress idempotently. Parent event
progress can be recomputed from decomposition weights.

## 8. Resources

Scalar resources:

```text
money.jpy, energy, mood, focus, inspiration, study_progress
```

Entity resources:

```text
inventory_items, inventory_movements, meal_records
```

Resources must be defined before mutation unless a Canon policy explicitly
allows ad-hoc resources.

## 9. TruthSource

Canon bindings define external truth domains:

```text
weather -> user_current_location / external_tool
clock -> system_clock
currency -> fixed_setting or external_tool
market_price -> external_tool
```

`life_truth` supports list/resolve/observe/bind. Observations are cached with
TTL and traced.

## 10. Heartbeat

Heartbeat uses WakeJobs and idempotency keys. It no-ops during paused/setup
states. It records time truth and can refresh Canon-bound TruthSources.

## 11. Trace

Every transaction is inspectable:

```text
/life trace latest
/life trace verify
/life trace receipts
/life trace explain <trace_id|tx_id|event_id>
```

LifeJournal uses a hash chain (`prev_hash -> entry_hash`) for tamper-evident
inspection.

## 12. Hermes implementation

The plugin remains embedded and adapter-based:

```text
plugin.yaml + __init__.py/register(ctx)
ctx.register_tool(...)
ctx.register_hook(pre_llm_call, transform_llm_output, post_tool_call)
ctx.register_command("life", ...)
ctx.register_cli_command("lifeengine", ...)
SQLite + FTS5 + sqlite-vec under $HERMES_HOME/lifeengine/lifeengine.db
```

No Hermes main loop fork is required. A future optional core patch can add a
mandatory fail-closed final gate for formal strong consistency.

## 13. v0.6 implemented status

Implemented:

```text
Life Canon setup/pause
LifeOps unified mutation path
CommitReceipt / FinalGate
Scalar resources and ledger
Inventory / meals
User confirmation flow
TruthSource resolve/observe/cache/trace
WakeJob heartbeat
Goals / life arcs / event decomposition / milestones / reflections
Trace verify and explain
```

Next recommended v0.7:

```text
Autonomy policy engine
Planner / Scheduler integration with goals
Proactive delivery policy
Goal review scheduler
Better natural-language extraction into LifeOps
Formal mandatory final gate patch for Hermes
```
