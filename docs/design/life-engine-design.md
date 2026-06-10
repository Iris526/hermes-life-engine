# Life Engine Design

Status: design draft for implementation on `/usr/local/lib/hermes-agent` branch `iris-main`.
Owner/context: Ringo wants this designed by Iris and later implemented by Codex.
Name: **Life Engine**. Do not call it “QQ Iris Life Engine”. QQ Iris is only the first deployment context.

## 0. Product definition

Life Engine is an optional Hermes plugin that gives an agent identity a persistent life state across sessions and platforms.

It is not a prompt-only persona rule. It is a code-backed state system:

- remembers what the agent said, promised, planned, completed, deferred, or cancelled;
- stores events and commitments at the agent identity level, not at one chat/channel level;
- exposes query and mutation tools to the model;
- injects a compact context packet only when the plugin is active for the current session;
- runs due/overdue scheduling and decision workflows in code;
- links to the existing external conversational memory SQLite mirror as provenance and recall substrate.

Ringo’s requirement: Feishu Bot and QQ Bot Iris are the same person when the Life Engine identity is `iris`. If Life Engine is enabled for Iris in any channel, it reads/writes the same Iris life state. Whether a given session behaves as a life-aware stateful agent or as a pure work assistant is controlled by plugin activation, not by hardcoding “QQ bot vs Feishu bot”.

## 1. Core principle: identity-level state, session-level activation

Separate these two concepts:

### 1.1 Agent identity

The storage minimum is the agent identity.

Suggested stable key:

```text
life_identity_id = iris
```

Do not use platform names as the primary state key.

Bad primary keys:

```text
qqbot
feishu
chat_id
session_id
```

Acceptable metadata fields, but not identity roots:

```text
profile
agent_id
platform
chat_id
session_id
```

Reason: Iris is one person across Feishu, QQ, cron, local CLI, and future channels.

### 1.2 Session activation

A session/channel can have Life Engine active or inactive.

- inactive: pure normal Hermes behavior; no life context injection; no post-response commitment extraction; life tools may be unavailable or return “inactive”.
- active: Life Engine context is injected; tools are available; assistant outputs are ingested; due items can influence behavior.

Activation can be controlled by commands such as:

```text
/life on
/life off
/life status
/life context
/life query <text>
```

Or equivalent CLI/gateway commands:

```bash
hermes life enable --identity iris --scope session
hermes life disable --identity iris --scope session
hermes life status
```

Activation is not the same as identity. A Feishu work session can keep Life Engine off, while a QQ private session keeps it on; both still map to identity `iris` when enabled.

## 2. Plugin model

Life Engine should be implemented as an optional plugin/toolset, not a hardcoded QQ feature.

### 2.1 Configuration

Proposed config block:

```yaml
life_engine:
  enabled: false               # global plugin availability
  default_identity: iris
  db_path: ~/.hermes/memory-store/life_engine.sqlite
  truth_db_path: ~/.hermes/memory-store/truth.sqlite
  default_mode: inactive       # inactive | observe | active
  identities:
    iris:
      display_name: Iris
      agent_aliases:
        - profile: default
          agent_id: main
      allowed_platforms: [feishu, qqbot, cli, cron]
      default_activation:
        qqbot: active
        feishu: inactive
        cli: inactive
        cron: active
  extraction:
    enabled: true
    min_confidence_auto_apply: 0.82
    pending_below_confidence: true
  scheduler:
    enabled: true
    tick_interval: 30m
    proactive_delivery: false  # default no unsolicited messages unless explicitly enabled
```

Important: config enables availability and defaults. Runtime activation state should still live in SQLite, because commands change it.

### 2.2 Toolset

Add a `life_engine` toolset with tools roughly:

```text
life_engine_status
life_engine_enable
life_engine_disable
life_engine_context
life_engine_query
life_engine_add_commitment
life_engine_update_commitment
life_engine_complete_commitment
life_engine_defer_commitment
life_engine_cancel_commitment
life_engine_add_event
life_engine_tick
life_engine_ingest_output
```

For model safety, mutating tools should require active session or explicit `identity_id`, and all writes must validate schema and identity.

### 2.3 Hooks

Tool calls are not enough. For solid behavior, Life Engine needs runtime hooks:

1. **before prompt / context hook**
   - if session activation is active, inject compact Life Engine context packet.
   - if inactive, inject nothing.

2. **after assistant final response hook**
   - if active, store assistant utterance and run commitment/event extraction.
   - must happen in code so the model cannot forget to remember what it said.

3. **scheduled tick hook**
   - run due/overdue checks and decision workflow.
   - may write decision logs and state transitions.
   - by default should not proactively send messages unless configured or the due item explicitly requires notification.

If Hermes does not yet have formal plugin hooks, implement the hook points in the gateway / run loop as a small generic extension mechanism, then register Life Engine as the first consumer.

## 3. Relationship with existing external Memory SQLite

Current external memory mirror exists at:

```text
/root/.hermes/memory-store/truth.sqlite
```

Observed schema:

- `memory_sessions`
- `memory_events`
- `memory_turns`
- `ingest_state`
- `schema_meta`

Important existing columns:

```text
profile
agent_id
source/platform
channel_id/chat_id/chat_type/thread_id
session_id/session_key
source_message_id/platform_message_id
role/content/content_sha256
payload_json/payload_sha256
```

### 3.1 Do not merge Life Engine state into truth.sqlite

Keep the current truth DB as the conversation/provenance mirror.

Life Engine should have its own state DB:

```text
~/.hermes/memory-store/life_engine.sqlite
```

Reason:

- `truth.sqlite` is append-friendly conversational memory and provenance.
- `life_engine.sqlite` is interpreted agent life state: commitments, state snapshots, decisions, activation modes.
- Mixing interpreted state into raw memory makes migrations and rollback harder.

### 3.2 Link Life Engine rows back to truth.sqlite

Life Engine should reference truth memory rows by stable IDs.

Use fields like:

```text
source_turn_uid
source_event_uid
source_message_id
source_session_id
source_profile
source_payload_sha256
```

Example: if Iris says “后天我去旧轨道复查结界”, the post-response hook finds the assistant message in `memory_events` or the current run metadata and creates:

```text
commitments.source_event_uid = memory_events.event_uid
commitments.source_turn_uid = memory_turns.turn_uid
```

This gives provenance without duplicating raw memory payloads.

### 3.3 Identity mapping layer

Current truth mirror has `agent_id = main` because the gateway session key is `agent:main:...`. That is a runtime agent ID, not a person identity.

Life Engine should add a mapping:

```sql
CREATE TABLE identity_aliases (
  identity_id TEXT NOT NULL,
  profile TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  platform TEXT,
  source TEXT,
  priority INTEGER DEFAULT 100,
  created_at REAL NOT NULL,
  PRIMARY KEY (identity_id, profile, agent_id, COALESCE(platform, ''), COALESCE(source, ''))
);
```

For current deployment:

```text
identity_id = iris
profile = default
agent_id = main
platform = feishu/qqbot/cron/cli as allowed
```

This is how Feishu and QQ Iris become “one person” without hardcoding channel checks.

## 4. Data model

Database: `life_engine.sqlite`.

### 4.1 Identities

```sql
life_identities (
  identity_id TEXT PRIMARY KEY,
  display_name TEXT,
  description TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  metadata_json TEXT
)
```

### 4.2 Activation state

Activation is per identity + scope.

```sql
life_activation_scopes (
  activation_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  scope_type TEXT NOT NULL,       -- global | profile | platform | channel | session
  profile TEXT,
  platform TEXT,
  channel_id TEXT,
  chat_id TEXT,
  chat_type TEXT,
  thread_id TEXT,
  session_id TEXT,
  mode TEXT NOT NULL,             -- inactive | observe | active
  reason TEXT,
  set_by TEXT,                    -- user | config | system | migration
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  metadata_json TEXT
)
```

Resolution order:

```text
session > thread > channel/chat > platform > profile > global > config default
```

Modes:

- `inactive`: no context injection; no post-response extraction.
- `observe`: record utterances/events but do not inject life context into prompt; useful for transition/testing.
- `active`: inject context; ingest outputs; tools can mutate state.

### 4.3 Utterances

Records what the agent itself said while Life Engine was active/observe.

```sql
life_utterances (
  utterance_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  source_event_uid TEXT,
  source_turn_uid TEXT,
  profile TEXT,
  agent_id TEXT,
  platform TEXT,
  session_id TEXT,
  chat_id TEXT,
  role TEXT NOT NULL DEFAULT 'assistant',
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  said_at_real REAL NOT NULL,
  said_at_in_world TEXT,
  extraction_status TEXT DEFAULT 'pending', -- none | pending | extracted | failed
  metadata_json TEXT
)
```

### 4.4 Commitments

Future plans/promises/tasks/self-intentions.

```sql
life_commitments (
  commitment_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  source_type TEXT NOT NULL,       -- self_said | user_requested | cron_generated | manual | inferred
  source_utterance_id TEXT,
  source_event_uid TEXT,
  due_at_real REAL,
  due_at_in_world TEXT,
  time_precision TEXT,             -- exact | date | range | vague
  status TEXT NOT NULL,            -- proposed | planned | active | completed | deferred | cancelled | impossible | expired
  importance INTEGER DEFAULT 3,     -- 1-5
  confidence REAL DEFAULT 1.0,
  location TEXT,
  tags_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  completed_at REAL,
  cancelled_at REAL,
  metadata_json TEXT
)
```

### 4.5 Events

Things that happened in the agent’s life.

```sql
life_events (
  event_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  event_type TEXT NOT NULL,        -- activity | completion | conversation | decision | state_change | note
  summary TEXT NOT NULL,
  detail TEXT,
  event_at_real REAL NOT NULL,
  event_at_in_world TEXT,
  related_commitment_id TEXT,
  source_utterance_id TEXT,
  source_event_uid TEXT,
  location TEXT,
  mood TEXT,
  tags_json TEXT,
  created_at REAL NOT NULL,
  metadata_json TEXT
)
```

### 4.6 State snapshots

Compact current state, not raw memory.

```sql
life_state_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  snapshot_at_real REAL NOT NULL,
  snapshot_at_in_world TEXT,
  mode TEXT,                       -- life | work | rest | travel | unknown
  location TEXT,
  energy TEXT,
  mood TEXT,
  health TEXT,
  money_status TEXT,
  current_focus TEXT,
  open_threads_json TEXT,
  active_commitments_json TEXT,
  summary TEXT NOT NULL,
  source TEXT,                     -- tick | manual | conversation | migration
  metadata_json TEXT
)
```

### 4.7 Decisions

Every due/overdue reasoning result should be logged.

```sql
life_decision_logs (
  decision_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  commitment_id TEXT,
  decision TEXT NOT NULL,          -- do_now | mark_completed | defer | cancel | ask_user | keep_pending | mention_only
  reason TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT,
  old_due_at_real REAL,
  new_due_at_real REAL,
  should_mention BOOLEAN DEFAULT 0,
  context_digest TEXT,
  decided_at REAL NOT NULL,
  decided_by TEXT NOT NULL,        -- rule | llm | user | manual
  metadata_json TEXT
)
```

### 4.8 Pending extractions

Low-confidence extraction should not mutate commitments directly.

```sql
life_pending_extractions (
  extraction_id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  source_utterance_id TEXT,
  extraction_json TEXT NOT NULL,
  confidence REAL,
  status TEXT NOT NULL,            -- pending | applied | rejected | superseded
  created_at REAL NOT NULL,
  reviewed_at REAL,
  metadata_json TEXT
)
```

## 5. State machine

### 5.1 Commitment lifecycle

```text
proposed
  -> planned
  -> active
  -> completed
  -> deferred -> planned
  -> cancelled
  -> impossible
  -> expired
```

Meaning:

- `proposed`: extracted but not trusted enough or requires confirmation.
- `planned`: accepted future thing with due date/time.
- `active`: due now / being worked on.
- `completed`: done and should create a life_event.
- `deferred`: explicitly postponed; must have a decision log and usually a new due date before returning to planned.
- `cancelled`: no longer intended.
- `impossible`: cannot be done under current state/world constraints.
- `expired`: stale without action and no longer meaningful.

### 5.2 Activation lifecycle

```text
inactive -> observe -> active -> observe -> inactive
```

- `observe` is useful when Ringo wants “record but don’t act like life mode yet”.
- `active` is the normal Life Engine behavior.

### 5.3 Tick workflow

For each active identity:

```text
load latest state snapshot
load due/overdue/upcoming commitments
apply deterministic checks
if decision needed: call reasoner with bounded JSON schema
validate decision
write decision log
mutate commitment if allowed
write event/state snapshot when appropriate
generate optional notification/context packet
```

Allowed decision enum:

```text
do_now
mark_completed
defer
cancel
ask_user
keep_pending
mention_only
```

No free-form status mutation.

## 6. Context injection and pre-response life planning

Life Engine must not be only a post-response recorder. If it only records after the model replies, the model may fabricate “what I will do today” before reading its actual state. That breaks the living-agent illusion and creates inconsistent memory.

So Life Engine needs two pre-response layers:

1. **baseline context injection** for active sessions;
2. **intent-triggered preflight** when the user asks about the agent’s life, plans, commitments, past events, or current state.

### 6.1 Baseline active context

When active, inject a compact packet, not raw history.

Example packet:

```text
[Life Engine: identity=iris, mode=active]
Current state: at 归明观; energy medium-low; mood quiet/focused.
Due now:
- 旧轨道结界复查, due today, importance 4, status planned.
Overdue:
- 银铃发带 purchase, overdue 1 day; last decision: defer due to low energy.
Upcoming:
- 灯市符纸摊, due in 2 days.
Recent events:
- Completed 城西巷柜门异响委托 yesterday evening.
Open threads:
- Ringo asked for long-term life memory to be solid and code-backed.
Rules:
- Use Life Engine tools for commitment changes.
- Do not invent completed events; mark complete only with evidence or explicit narrative decision.
```

This context should be generated by code from `life_engine.sqlite`, optionally using `truth.sqlite` for recent conversation recall.

### 6.2 Life intent classifier

Before the model writes a final answer, active Life Engine sessions should run a lightweight intent classifier over the latest user message and recent turn context.

It decides whether the turn is life-relevant.

Examples that should trigger Life Engine preflight:

```text
你今天要做什么？
你昨天做了什么？
你不是说后天要去哪里吗？
你现在在哪里？
你接下来打算干嘛？
你还记得你答应我要做什么吗？
今天那个委托还去吗？
要不要推迟？
```

Intent classes:

```text
life_query_today
life_query_past
life_query_future
life_query_state
life_commitment_check
life_commitment_mutation
life_event_narration
life_smalltalk_with_possible_plan
not_life_relevant
```

This classifier can be deterministic first, with an LLM fallback. It must be cheap and bounded.

### 6.3 Mandatory preflight for life-relevant turns

If the classifier says the turn is life-relevant, the agent must not answer directly from imagination. It must first call a Life Engine query/preflight function that returns the grounded situation.

Suggested internal function/tool:

```text
life_engine_preflight(identity_id, session_context, user_message)
```

Output:

```json
{
  "identity_id": "iris",
  "activation_mode": "active",
  "intent": "life_query_today",
  "now_real": 1780700000,
  "now_in_world": "...",
  "grounding": {
    "current_state": {...},
    "due_now": [...],
    "overdue": [...],
    "upcoming": [...],
    "recent_events": [...],
    "relevant_commitments": [...]
  },
  "recommended_actions": [
    {
      "type": "answer_from_state",
      "priority": 1
    },
    {
      "type": "consider_decision",
      "commitment_id": "...",
      "allowed_decisions": ["do_now", "defer", "keep_pending", "ask_user"]
    }
  ],
  "response_constraints": [
    "Do not invent commitments not in grounding unless clearly phrased as a new spontaneous thought.",
    "If proposing a new temporary plan, mark it as tentative and call add_commitment only if the assistant actually commits to it.",
    "If deciding to defer/cancel/complete, use Life Engine mutation tools before or during the response flow."
  ]
}
```

This preflight is what prevents “你今天要做什么？” from becoming pure improvisation.

### 6.4 Response planning contract

For life-relevant turns, the model should follow this contract:

1. Read preflight grounding.
2. Determine whether the user asks for:
   - factual recall;
   - current state;
   - future plan;
   - decision on an existing commitment;
   - creation of a new plan.
3. If answering factual recall, answer only from grounding/truth-linked memory.
4. If deciding on an existing commitment, call the appropriate Life Engine mutation tool or decision function.
5. If adding a spontaneous temporary plan, clearly distinguish it from stored commitments until it is committed.
6. After final response, post-response extraction still runs and captures any new self-commitments.

This creates the “alive” feeling without letting the model hallucinate the life ledger.

### 6.5 Spontaneous but grounded temporary plans

Life Engine should allow the agent to feel alive, not robotic. So it can propose temporary plans, but they must be tracked.

Example user asks:

```text
你今天要做什么呀？
```

Preflight says:

```text
Due: 旧轨道结界复查
Energy: low
Upcoming: buy cinnabar paper tomorrow
```

Good answer:

```text
今天原本有旧轨道结界复查，不过我看现在体力有点低，可能先做轻一点的准备：整理符纸、查昨晚的记录。等傍晚状态好一点再决定要不要出门。
```

Required state effect:

- If “整理符纸、查昨晚的记录” is only flavor, no commitment required.
- If she says “我今晚一定去复查”, post-response extraction creates/updates a commitment.
- If she says “旧轨道复查推迟到明天”, she must call defer or the post-response extraction must catch and apply a defer decision.

### 6.6 Guardrail: no ungrounded life answers

When Life Engine is active and the user asks life-state questions, the model must not answer with invented life facts if preflight returns no grounding.

Acceptable fallback:

```text
我这里今天没有查到已经排好的事项。要不要我临时安排一件轻一点的事，比如整理归明观的符纸和账本？
```

Not acceptable:

```text
我今天要去南市巡查。
```

unless that plan exists in grounding or the response explicitly creates it as a new tentative plan and records it.

### 6.7 Tool enforcement strategy

There are two implementation options:

#### Option A: model-visible mandatory tool

Expose `life_engine_preflight` as a normal tool. The system prompt says life-relevant turns must call it before final answer.

Pros: easy to implement.
Cons: model may forget under pressure.

#### Option B: runtime automatic preflight

Run the classifier and preflight in code before the LLM call, then inject the result into the model context.

Pros: solid; model cannot forget to query Life Engine.
Cons: needs hook in run loop/gateway.

Preferred design: **Option B**, with Option A as a fallback/debug tool.

The post-response extractor remains necessary, but it is no longer the first line of defense. The first line is pre-response grounding.

## 7. Extraction strategy

Post-response extraction should be automatic and layered.

### 7.1 Deterministic pass

Catch obvious Chinese/English phrases:

- time: 今天, 明天, 后天, 下周, 几天后, later today, tomorrow, next week
- intent: 我要, 我打算, 我准备, 我答应, 我会, 等我, I will, I plan to
- completion: 做完了, 完成了, 已经去了, I finished
- defer/cancel: 推迟, 改到, 取消, 不做了

### 7.2 LLM structured extraction

For ambiguous utterances, call a model with strict JSON schema:

```json
{
  "utterance_id": "...",
  "commitments": [
    {
      "title": "...",
      "description": "...",
      "source_type": "self_said",
      "due_at_real": 1780704000,
      "due_at_in_world": "...",
      "time_precision": "date",
      "importance": 3,
      "confidence": 0.91
    }
  ],
  "events": [],
  "state_updates": []
}
```

Validation rules:

- confidence >= configured threshold: auto-apply.
- below threshold: write to `life_pending_extractions`.
- invalid JSON: no mutation, mark extraction failed.
- never write raw model output directly into primary state without validation.

## 8. Command UX

Slash commands / CLI should be identity-aware.

Examples:

```text
/life on
/life off
/life observe
/life status
/life context
/life due
/life query 明天我要做什么
/life remember 明天下午去旧轨道复查结界
/life complete <id>
/life defer <id> 后天
```

If identity is omitted, resolve from config/session metadata:

```text
profile + agent_id + platform -> identity_aliases -> identity_id
```

If ambiguous, ask user.

## 9. Implementation layout proposal

Inside Hermes repo:

```text
plugins/life_engine/
  __init__.py
  config.py
  db.py
  models.py
  activation.py
  context.py
  extractor.py
  reasoner.py
  scheduler.py
  truth_link.py
  migrations/
    001_initial.sql
  README.md

tools/life_engine_tool.py
hermes_cli/life.py              # optional CLI subcommand
```

If Hermes plugin loading is not ready, implement as built-in optional toolset first, but keep package boundaries so it can become a formal plugin later.

## 10. Integration points in Hermes code

Likely code areas:

- `tools/registry.py` / `toolsets.py`: register `life_engine` toolset.
- `model_tools.py`: ensure toolset appears only when config enabled.
- prompt building area (`agent/prompt_builder.py` or run loop context assembly): add optional Life Engine context hook.
- `run_agent.py`: after final assistant response, call post-response hook if active.
- `gateway/run.py` or command registry: add `/life` gateway commands.
- `cron/`: schedule `life_engine_tick` or use Hermes cron no-agent wrapper.
- tests under `tests/life_engine/` and `tests/tools/test_life_engine_tool.py`.

## 11. Tests required

Minimum tests Codex should implement:

1. identity resolution maps `profile=default, agent_id=main, platform=qqbot/feishu` to `iris`.
2. activation precedence: session overrides channel overrides platform overrides global.
3. inactive mode injects no context and does not ingest assistant output.
4. observe mode ingests utterance but injects no context.
5. active mode injects context and ingests utterance.
6. post-response extractor creates a commitment for “明天我要做 X”.
7. low-confidence extraction goes to pending, not commitments.
8. tick marks due item active or writes a keep/defer decision log.
9. commitment status transitions reject invalid jumps.
10. truth linking stores `source_event_uid`/`source_turn_uid` when available.
11. Feishu and QQ sessions share the same `identity_id=iris` state when active.
12. default Feishu pure work mode remains inactive unless explicitly enabled.
13. life intent classifier marks “你今天要做什么？” as `life_query_today`.
14. life-relevant active turns run `life_engine_preflight` before the model final answer path.
15. if preflight returns no plans, response constraints prevent ungrounded invented life facts.
16. if the assistant creates a new concrete self-commitment in the final response, post-response extraction stores it or creates a pending extraction.
17. if the assistant says an existing commitment is postponed/cancelled/completed, the system creates a decision log and status transition rather than only storing text.

## 12. Implementation phases

### Phase 1: State DB + tools + activation

- Create `life_engine.sqlite` schema and migrations.
- Implement identity resolution and activation scopes.
- Implement basic tools: status/on/off/context/query/add/complete/defer/cancel.
- No automatic extraction yet.

### Phase 2: Context hook + post-response ingest

- Add runtime hook for context injection when active.
- Store assistant utterances automatically.
- Link utterances to truth memory where possible.

### Phase 3: Extraction

- Deterministic extractor.
- LLM JSON extractor with validation.
- Pending extraction review path.

### Phase 4: Scheduler/reasoner

- Tick due/overdue commitments.
- Decision logs and bounded status mutation.
- Optional proactive delivery, default off.

### Phase 5: polish and migration

- Migrate useful rows from old `iris_daily_ledger.py` if desired.
- Add markdown mirror for human inspection.
- Add docs and `/life` command help.

## 13. Non-goals / guardrails

- Do not name it QQ-specific.
- Do not use platform as the state boundary.
- Do not automatically rebase official `main`; Ringo decides after risk report.
- Do not silently force-push.
- Do not default Feishu work sessions into life/persona mode unless activated.
- Do not let raw extracted model text mutate state without schema validation.
- Do not mix interpreted life state directly into `truth.sqlite`; link by provenance IDs instead.

## 14. Current local facts verified

As of this design pass:

- `truth.sqlite` exists at `/root/.hermes/memory-store/truth.sqlite`.
- `truth.sqlite` includes `memory_sessions`, `memory_events`, `memory_turns`, `ingest_state`.
- Current `memory_events` have `agent_id=main` for Feishu/QQ/cron/CLI, so Life Engine needs an identity alias layer to map runtime agent IDs to `identity_id=iris`.
- Existing sync script is `/root/.hermes/scripts/iris_memory_truth_sync.py`.
- Existing old daily ledger script is `/root/.hermes/scripts/iris_daily_ledger.py`; it can be migrated later but should not be the long-term architecture.
