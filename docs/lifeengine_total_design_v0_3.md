# LifeEngine 总设计文档 v0.3

> LifeEngine 是一个嵌入式 Agent 生活内核。它不是单纯的 memory plugin，也不是外挂后台服务，而是一个可暂停、可设定、可追踪、可审计、可迁移的 Agent/User Life Runtime。

本文档是截至 v0.3 的总设计文档，覆盖理念、概念、架构、闭环机制、Trace、Hermes 插件实施方案与后续路线。

---

## 1. 核心定义

LifeEngine 的目标是让某个 Agent 拥有持续的“自我生活”：

- 有自己的身份、人设、世界观、真相源规则。
- 有自己的时间、日程、睡眠、饮食、计划、目标、复盘。
- 有自己的资源，不只是货币，还包括精力、心情、灵感、衣柜、技能、关系等。
- 有自己的事件、行动、结果、日记、主动分享意图。
- 能跨会话记住自己说过什么、计划过什么、做过什么、推迟过什么。
- 所有生活状态都可追踪、可解释、可回放、可校验。

一句话定义：

> 模型负责想象和表达，LifeEngine 负责把想象变成可校验、可推进、可追溯的生活现实。

---

## 2. 核心理念

### 2.1 Canon First

Life Canon 是 Agent 生活内部的最高真相源。普通记忆、对话上下文、模型生成都不能覆盖 Canon。

Canon 包含：

- Identity Canon：它是谁。
- Worldview Canon：它生活在哪个世界，是否和用户同城、同天气、同时间。
- Truth Source Canon：天气、货币、地点、时钟、市场价等参考什么来源。
- Resource Canon：有哪些资源、资源范围、恢复/消耗规则。
- Behavior Canon：能不能补写过去、能不能主动聊天、能不能自发行动。
- Schedule Canon：睡眠、日常节律、工作时间、空闲时间规则。
- Diary Canon：是否写日记、写日记的频率和隐私级别。
- User Life Policy：用户侧生活事实是否需要确认。

### 2.2 State Before Narrative

Agent 不能先说生活事实、再随便记一下。正确顺序是：

```text
Proposed LifeOps
  ↓
Validation
  ↓
Transaction Commit
  ↓
CommitReceipt
  ↓
Final Answer
```

最终回答里的生活 claim 必须来自：

```text
canonical life state ∪ current-turn CommitReceipt facts
```

### 2.3 Pause Means No Mutation

LifeEngine 有独立状态机。暂停态不是“少做一点”，而是硬禁止生活突变。

在以下状态中禁止生活推进和生活写入：

```text
paused
paused_setup
read_only
disabled
migrating
archived
```

设定态只写 CanonDraft，不写事件、日记、资源流水或记忆。

### 2.4 Resource Conservation

资源不只是钱包。任何行动都可以消耗、产生、占用、恢复或转换资源。

资源包括：

- money.jpy / money.usd / points
- time.available / schedule blocks
- energy / focus / mood / stress / inspiration
- wardrobe / inventory items
- skill.progress
- relationship.trust / relationship.attention
- permission / tool budget

所有资源变化都必须写 Resource Ledger。

### 2.5 Heartbeat Progression

Agent 生活不应该只在用户提问时一次性补完。Heartbeat 是独立推进器。

Heartbeat 可以关闭、手动、Hermes cron、嵌入式线程、宿主框架驱动。

v0.3 采用 wake-job 模型：

```text
ScheduleBlock created
  ↓
WakeJob created
  ↓
Heartbeat claims due WakeJob
  ↓
Event/Action executes idempotently
  ↓
Result + Resource + Memory + Receipt
```

### 2.6 Trace Everything

每个重要行为都要有 trace。

Trace 不只是 debug log，而是解释系统：

- 这件事为什么存在？
- 它来自用户话语、模型计划、heartbeat、truth source，还是 Canon migration？
- 用的是哪个 Canon version？
- 经过了哪些 validator？
- 影响了哪些资源？
- 写入了哪些 journal entry？
- 最终回答中的哪句话引用了它？

---

## 3. LifeEngine 总体架构

```text
LifeEngine
  ├── Control Plane
  │     ├── Engine State Machine
  │     ├── Module Gates
  │     ├── Pause / Resume
  │     ├── Setup Mode
  │     ├── Workspace Switching
  │     └── Canon Draft / Commit
  │
  ├── Canon Layer
  │     ├── Life Canon Store
  │     ├── CanonDraft Extractor
  │     ├── Canon Versioning
  │     ├── Canon Migration Planner
  │     └── Truth Source Binding
  │
  ├── LifeOps Transaction Layer
  │     ├── LifeOps Validator
  │     ├── Transaction Manager
  │     ├── CommitReceipt
  │     ├── FinalGate
  │     └── Journal Writer
  │
  ├── Resource Layer
  │     ├── Resource Registry
  │     ├── Resource Accounts
  │     ├── Resource Ledger
  │     ├── Reservation Manager
  │     └── Resource Reconcile
  │
  ├── Time / Heartbeat Layer
  │     ├── Time Normalizer
  │     ├── Schedule Blocks
  │     ├── Wake Jobs
  │     ├── Schedule Execution Records
  │     └── Heartbeat Tick
  │
  ├── Behavior Layer
  │     ├── Events
  │     ├── Actions
  │     ├── Results
  │     ├── Goals / Arcs (planned)
  │     ├── Inventory / Meals (planned)
  │     └── Autonomy / Serendipity (planned)
  │
  ├── Memory Layer
  │     ├── Structured Memory
  │     ├── FTS5
  │     ├── sqlite-vec
  │     └── Diary
  │
  ├── Proactive Layer
  │     ├── Proactive Intent
  │     ├── Outbox
  │     ├── Delivery Policy
  │     └── Relationship Relevance
  │
  ├── Trace Layer
  │     ├── Trace Runs
  │     ├── Trace Spans
  │     ├── Life Transactions
  │     ├── Life Ops
  │     ├── Life Journal
  │     ├── Audit Log
  │     └── Hash Chain Verification
  │
  └── Adapter Layer
        ├── Hermes Plugin Adapter
        └── Future Agent Loop Adapters
```

---

## 4. 工作区模型

LifeEngine 机制可以切换到用户侧，但真相策略不同。

### 4.1 Agent Self Workspace

Agent 自己的生活。

允许：

- Agent 叙事生成自己的计划。
- Agent 补写自己的过去空白。
- Heartbeat 推进 Agent 自己的事件。
- Agent 写自己的日记。
- Agent 产生主动分享意图。

### 4.2 User Life Workspace

用户的真实生活。

不允许：

- 模型编造用户过去。
- 模型自动完成用户计划。
- 模型擅自写用户事实。

用户侧事实来源必须是：

```text
user_reported
user_confirmed
tool_imported
calendar_imported
file_imported
manual_entry
```

### 4.3 Relationship Workspace

Agent 与某个用户之间的关系记忆。

用于：

- 这个用户关心过 Agent 的哪些生活事件。
- Agent 对这个用户有哪些承诺。
- 用户是否允许主动聊天。
- 用户对打扰的敏感度。

---

## 5. 控制状态机

```text
uninitialized
  ↓
setup_required
  ↓
setup
  ↓
active
  ├── paused
  ├── paused_setup
  ├── read_only
  ├── migrating
  ├── disabled
  └── archived
```

关键规则：

| 状态 | 允许行为 | 禁止行为 |
|---|---|---|
| setup_required | 提示用户设定 | 生活推进 |
| setup | 写 CanonDraft | Event/Resource/Diary 生活写入 |
| active | 正常 LifeOps / heartbeat | 违反 Canon 的写入 |
| paused | 读取、trace、setup | mutation、heartbeat |
| paused_setup | CanonDraft 编辑 | 生活流水污染 |
| read_only | 查询、trace | 所有 mutation |
| disabled | 数据保留，不注入 | 所有运行 |

---

## 6. LifeOps 统一写入模型

v0.3 的重要收敛是：所有 durable mutation 都走 LifeOps。

LifeOps 示例：

```json
{
  "type": "CREATE_EVENT",
  "payload": {
    "title": "明天下午买裙子",
    "status": "planned",
    "source": "agent_prediction"
  }
}
```

统一流程：

```text
Tool / Heartbeat / Setup
  ↓
LifeOps proposal
  ↓
validate_life_ops
  ↓
life_transactions row
  ↓
life_ops rows
  ↓
_apply_op
  ↓
life_journal append
  ↓
commit_receipt
  ↓
committed facts
```

不再允许 `life_event create`、`life_resource delta`、`life_memory remember` 直接写数据库。

---

## 7. CommitReceipt 与 FinalGate

每次 LifeOps transaction 生成 CommitReceipt：

```json
{
  "receipt_id": "receipt_xxx",
  "transaction_id": "tx_xxx",
  "facts": [
    {
      "kind": "event",
      "claim": "明天下午买裙子 status=planned",
      "evidence": {"event_id": "event_xxx"}
    }
  ]
}
```

FinalGate 流程：

```text
Final response
  ↓
_detect_life_claims
  ↓
receipt_facts_for_turn
  ↓
canonical_fact_texts
  ↓
claim_matches_evidence
  ↓
allow / block / trace-only
```

这样避免：

```text
模型提交了喝水，却最终说自己买了裙子。
```

---

## 8. 资源闭环

### 8.1 Resource Definition

资源必须先定义，才能 delta。

例：

```json
{
  "key": "inspiration",
  "display_name": "灵感值",
  "resource_class": "capacity",
  "min_value": 0,
  "max_value": 100,
  "initial": 50
}
```

### 8.2 Resource Account

当前值是 materialized view，用于快速读取。

### 8.3 Resource Ledger

Resource Ledger 是解释性真相源。

```text
opening + Σ ledger.delta = account.current_value
```

### 8.4 Resource Reconcile

v0.3 增加 reconcile：

```bash
hermes lifeengine resource reconcile
```

或：

```text
/life resource reconcile
```

它检查账户值和 ledger 总和是否一致，并写入 trace/audit。

---

## 9. 时间与 Heartbeat

### 9.1 Time Normalization

所有时间同时保留：

```text
ISO string: 用于显示和 trace
Epoch seconds: 用于排序、overlap、due 查询
Timezone: 用于解释本地时间
```

### 9.2 ScheduleBlock

```text
start / end
start_ts / end_ts
event_id
action_id
status
idempotency_key
```

### 9.3 WakeJob

```text
wake_at / wake_at_ts
reason
target_id
status
idempotency_key
claimed_by
running_at
completed_at
```

Heartbeat 不再盲扫 schedule，而是 claim due wake jobs。

---

## 10. Memory / Vector / Diary

LifeEngine 使用三层召回：

```text
1. Structured SQL
2. FTS5
3. sqlite-vec
```

sqlite-vec 是强依赖，不做可选 fallback。

Diary 不是 journal。

```text
Journal: 机器流水，不可省。
Diary: Agent 叙事日记，必须从 committed state 派生。
```

---

## 11. Trace 设计

Trace 由这些表组成：

- trace_runs
- trace_spans
- life_transactions
- life_ops
- life_journal
- audit_log
- commit_receipts
- commit_receipt_facts
- trace_integrity_checks

### 11.1 Hash Chain

life_journal 是 append-only hash chain：

```text
prev_hash + payload_hash → entry_hash
```

可通过：

```text
/life trace verify
```

验证。

### 11.2 Explain

```text
/life trace explain <tx_id>
/life trace explain <event_id>
/life trace explain <trace_id>
```

目标是回答：

```text
这件事为什么存在？
它经过哪些 LifeOps？
有哪些资源变化？
有哪些 receipt facts？
它是否被最终回答引用？
```

---

## 12. Hermes 插件实施方案

LifeEngine 以普通 Hermes directory plugin 接入。

```text
~/.hermes/plugins/lifeengine/
  plugin.yaml
  __init__.py
  tools.py
  hooks.py
  cli.py
  db.py
  runtime.py
  ...
```

注册能力：

- tools：life_status / life_control / life_setup / life_commit / life_resource / life_event / life_memory / life_tick / life_diary / life_trace
- hooks：pre_llm_call / transform_llm_output / post_tool_call / session hooks
- slash command：/life
- CLI：hermes lifeengine
- skill：lifeengine:lifeengine

Hermes 插件版边界：

> Hermes 当前会隔离插件 hook 异常并继续主循环。LifeEngine 会尽量在 transform_llm_output 返回安全替代文本以 fail-closed，但严格形式化闭环需要未来增加 mandatory final gate core patch。

---

## 13. 当前 v0.3 已实现

- v0.3 schema migration。
- 所有 mutation tools 统一转 LifeOps。
- CommitReceipt / receipt facts。
- FinalGate claim/evidence 对齐。
- OwnerScopeResolver。
- 时间规范化列。
- WakeJob heartbeat。
- 资源 delta 禁止 ad-hoc 污染。
- Resource reconcile。
- Trace explain 扩展。
- 测试覆盖：setup/commit/resume、user-life policy、resource reservation、receipt final gate、resource reconcile、wake job heartbeat。

---

## 14. 后续路线

### v0.4 TruthSource Layer

- Canon truth source binding resolver。
- Weather bridge。
- User current location binding。
- Tool observation trace。

### v0.5 User Life Confirmation

- user_confirmations 完整流程。
- /life confirm / reject。
- 用户日程、资源、计划的确认式写入。

### v0.6 Inventory / Meals / Goals

- inventory_items
- inventory_movements
- meal_records
- goals
- life_arcs
- event dependencies

### v0.7 Proactive Outbox

- ProactiveIntent scoring。
- quiet hours。
- daily proactivity budget。
- pending-only / manual-send / auto-send。

### v0.8 Framework Core Extraction

- lifeengine-core 与 lifeengine-hermes-adapter 分离。
- 支持其他 Agent Loop。

---

## 15. 最终闭环判断

概念架构已经闭环：

```text
Canon → TruthSource → LifeOps → Validator → Transaction → Receipt → Journal/Trace → State → Recall/FinalGate
```

v0.3 代码从“半闭环骨架”推进到了“核心写入闭环”：

```text
mutation path: closed
receipt path: closed
resource path: mostly closed
heartbeat path: wake-job closed
final audit: plugin-level closed, host-level fail-closed 仍需 Hermes core patch
```

下一步应先接 TruthSource，而不是继续重构底层。
