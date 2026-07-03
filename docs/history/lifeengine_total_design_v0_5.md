# LifeEngine Total Design v0.5

## 1. Definition

LifeEngine is an embedded, local-first life runtime for Agent frameworks. It
turns an Agent's self-life from prompt-only roleplay into a traceable state
system: Life Canon, resources, events, schedules, memories, heartbeat,
TruthSources, user confirmations, entity resources, diary, proactive intents,
and final-answer auditing.

The central rule is:

```text
No committed life state, no durable life claim.
```

A model may propose or narrate, but LifeEngine decides what becomes the Agent's
or user's canonical life.

## 2. Core principles

### Canon First

Life Canon is the Agent or user-life workspace's highest internal truth layer:
identity, worldview, behavior rules, truth-source bindings, resource definitions,
autonomy policy, proactive policy, and diary policy.

### Pause Means No Mutation

During `paused`, `paused_setup`, `read_only`, or `disabled`, LifeEngine does not
advance heartbeat, create events, write diary, consume resources, or retro-fill
life facts. Setup mode writes CanonDraft only.

### LifeOps as the only mutation path

All durable mutations go through:

```text
LifeOps -> Validator -> Transaction -> CommitReceipt -> Journal -> Trace -> State
```

Convenience tools such as `life_event`, `life_resource`, `life_inventory`, and
`life_diary` translate into LifeOps. They do not bypass the transaction service.

### Resource conservation

Scalar resources such as money, energy, mood, inspiration, focus, trust, and
skills use ResourceDefinition, ResourceAccount, ResourceReservation, and
ResourceLedger.

Entity resources such as clothes, books, supplies, and meals use first-class
inventory and meal tables. They are not flattened into counters only.

### State before narrative

Final response claims must be supported by canonical state or by CommitReceipt
facts from the current turn.

```text
final_claims ⊆ canonical_state ∪ current_turn_commit_receipts
```

### User Life requires confirmation

Agent self-life can use narrative reality according to Canon. User Life cannot
be invented by the Agent. Proposed user facts enter `user_confirmations` and only
become durable LifeOps after user confirmation.

## 3. Architecture

```text
LifeEngine
  ├── Control Plane
  │   ├── engine_state
  │   ├── module_gates
  │   ├── setup / paused_setup
  │   └── workspace scope
  │
  ├── Canon Layer
  │   ├── CanonDraft
  │   ├── CanonVersion
  │   ├── CanonMigration
  │   └── TruthSource bindings
  │
  ├── Transaction Layer
  │   ├── LifeOps
  │   ├── Validator
  │   ├── LifeTransaction
  │   ├── CommitReceipt
  │   └── FinalGate
  │
  ├── Time Layer
  │   ├── ScheduleBlock
  │   ├── WakeJob
  │   ├── Heartbeat
  │   └── ScheduleBlockExecution
  │
  ├── Resource Layer
  │   ├── scalar resources
  │   ├── resource ledger
  │   ├── resource reservations
  │   ├── inventory items
  │   ├── inventory movements
  │   └── meal records
  │
  ├── Memory Layer
  │   ├── memories
  │   ├── FTS5
  │   ├── sqlite-vec
  │   └── diary entries
  │
  ├── TruthSource Layer
  │   ├── resolve
  │   ├── observe
  │   ├── cache
  │   └── trace reads
  │
  ├── User Confirmation Layer
  │   ├── propose
  │   ├── list
  │   ├── confirm
  │   └── reject
  │
  └── Trace Layer
      ├── trace_runs
      ├── trace_spans
      ├── life_journal
      ├── audit_log
      └── hash-chain verification
```

## 4. Workspaces

LifeEngine uses the same schema across three workspaces but applies different
truth policies.

```text
agent_self:
  Agent's own self-life. Narrative reality is allowed if Canon permits it.

user_life:
  User's real life. Agent cannot invent facts. Requires user_reported,
  user_confirmed, tool_imported, calendar_imported, file_imported, or manual_entry.

relationship:
  Agent-user relationship state. Isolated from other users.
```

## 5. Control states

```text
uninitialized
setup_required
setup
active
paused
paused_setup
read_only
migrating
disabled
archived
```

Mutation-blocking states:

```text
setup, paused, paused_setup, read_only, disabled, archived, migrating
```

## 6. Life Canon

Canon is versioned. Edits enter CanonDraft and only become active after commit.
Major worldview changes can create migrations or branches.

Canon contains:

- identity
- worldview
- truth sources
- resource definitions
- schedule rules
- behavior rules
- autonomy policy
- proactive policy
- diary policy
- user-life policy

## 7. TruthSource layer

TruthSource lets Canon bind external facts to resolvers.

Example:

```json
{
  "domain": "weather",
  "authority": "user_current_location",
  "freshness_ttl_minutes": 120,
  "fallback": "unknown"
}
```

TruthSource operations:

```text
list
resolve
observe
bind
```

TruthSource writes:

```text
truth_source_reads
truth_source_cache
trace_spans
life_journal
```

TruthSource does not itself create life events. It creates observed evidence that
planners, heartbeat, and LifeOps can cite.

## 8. Scalar resource layer

Scalar resources are ledger-backed quantities:

- money.jpy
- energy
- mood
- inspiration
- focus
- stress
- study_progress
- relationship.trust

Any scalar resource delta requires an existing ResourceDefinition unless Canon
explicitly allows ad-hoc resources.

Resource invariant:

```text
resource_accounts.current_value == sum(resource_ledger.delta)
```

`resource reconcile` verifies this invariant.

## 9. Entity resource layer in v0.5

Entity resources have identity. They answer questions like:

```text
What is in my wardrobe?
What did I eat for lunch?
Which supplies did I use up?
Which book did I buy?
```

### InventoryItem

```text
id
owner_kind / owner_id
name
category / subcategory
quantity / unit
attributes
condition
location
emotional_value
acquired_at
acquired_by_event_id
acquired_by_transaction_id
status
```

### InventoryMovement

```text
item_id
operation
quantity_delta
from_location / to_location
event_id / action_id / result_id / transaction_id
reason
source
```

### MealRecord

```text
meal_type
eaten_at
food_items
location
cost
satisfaction
notes
event_id
source
```

Meal records may optionally consume scalar resources such as `money.jpy`.

## 10. User Confirmation layer in v0.5

User Life cannot be written from Agent narrative sources. For uncertain user
facts, LifeEngine creates a pending confirmation.

Flow:

```text
Agent/user conversation implies possible user fact
  -> life_confirmation propose
  -> user_confirmations pending
  -> user approves or rejects
  -> approved ops become source=user_confirmed
  -> normal LifeOps commit
  -> CommitReceipt and Journal
```

This preserves one transaction model while preventing Agent-side hallucinations
from becoming user-life facts.

## 11. Heartbeat and WakeJobs

Heartbeat uses WakeJobs rather than one-shot catch-up.

```text
ScheduleBlock -> WakeJob -> claim -> execute -> complete/fail -> journal/trace
```

Heartbeat is module-gated and can be off/manual/Hermes cron/framework driven.
Paused states cause heartbeat no-op.

## 12. Memory and diary

Memories are stored structurally and indexed with FTS5 and sqlite-vec.
Diary entries are derived from committed life state. Diary may be narrative but
must be evidence-backed by events, results, resources, meals, memories, or other
canonical state.

## 13. Trace and explainability

LifeEngine records:

- TraceRun
- TraceSpan
- LifeTransaction
- LifeOps
- CommitReceipt
- LifeJournal
- AuditLog

LifeJournal uses a hash chain:

```text
prev_hash -> entry_hash
```

Explain should answer:

```text
Why does this fact exist?
Which Canon version was active?
Which LifeOps created it?
Which resources changed?
Which TruthSource reads were used?
Which receipt supported the final answer?
```

## 14. Hermes implementation

LifeEngine is a normal Hermes directory plugin:

```text
~/.hermes/plugins/lifeengine/plugin.yaml
~/.hermes/plugins/lifeengine/__init__.py
```

It registers:

- tools
- hooks
- slash command `/life`
- CLI command `hermes lifeengine`
- plugin skill `lifeengine:lifeengine`

It uses:

```text
pre_llm_call           -> inject LifeEngine context
transform_llm_output   -> FinalGate audit
post_tool_call         -> trace tool results
on_session_start/end   -> session trace
```

## 15. v0.5 implemented status

Implemented:

- setup / paused / active state machine
- CanonDraft and CanonVersion
- LifeOps transaction service
- CommitReceipt facts
- FinalGate claim/evidence audit
- scalar resource registry and ledger
- resource reservation and reconcile
- events, schedule blocks, wake jobs, heartbeat
- truth source resolve/observe/cache/bind
- memory FTS5 + sqlite-vec
- diary entries
- trace hash-chain verification
- user confirmation propose/confirm/reject
- inventory items and inventory movements
- meal records

Still future:

- richer goals/life arcs
- richer proactive delivery
- relationship scoring
- advanced planner/autonomy model
- full branch/fork state copying
- mandatory Hermes core final gate for formal fail-closed semantics

## 16. Closed-loop assessment

v0.5 closes the most important practical loops:

```text
Canon loop: closed
Pause/setup anti-pollution loop: closed
Mutation/LifeOps loop: closed
CommitReceipt/FinalGate loop: closed at plugin level
Scalar resource loop: closed with reconcile
Entity resource loop: closed for inventory and meals
TruthSource loop: closed for resolve/observe/cache/trace
Heartbeat loop: closed with wake jobs
User-life confirmation loop: closed
Trace integrity loop: closed with hash verification
```

The remaining formal limitation is Hermes hook fail-open behavior: a plugin hook
exception is isolated by the host. LifeEngine's FinalGate tries to fail closed in
its own code path, but a future Hermes core `mandatory_final_gate` would make the
contract formally enforceable.
