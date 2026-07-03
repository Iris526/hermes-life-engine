# LifeEngine 总设计文档 v0.9

> **v0.9 主题：Narrative Execution Simulator / Serendipity Engine。**
>
> v0.8 已经实现 Proactive Intent / Outbox / 主动聊天状态机。v0.9 进一步把 heartbeat 的事件推进从“到点完成”升级为“叙事执行决策”：每个到期 schedule block 先生成可追踪的 execution decision，再根据资源、依赖、TruthSource、天气、事件类型、重要性、Canon/module gate 决定 completed / partial / postponed / skipped / failed，并通过 LifeOps 事务提交。

---

## 1. 核心理念

LifeEngine 的核心不变量保持不变：

```text
模型负责想象和表达；LifeEngine 负责把想象变成可校验、可推进、可追溯的生活现实。
```

v0.9 增加一条执行层原则：

```text
Due schedule does not imply success.
```

也就是说，到了计划时间，不等于事件必然完成。Agent 的生活要像人一样受到这些因素影响：

```text
资源是否足够
依赖是否完成
天气/外部事实是否允许
心情/精力是否支持
事件是否重要
事件是否适合延期
历史上是否已有拖延
是否产生小问题、小发现、小意外
```

---

## 2. 当前完整架构

```text
LifeEngine
  ├── Control Plane
  │   ├── EngineState
  │   ├── ModuleGates
  │   ├── Pause / Setup / Resume
  │   └── Workspace Scope
  │
  ├── Canon Layer
  │   ├── CanonDraft
  │   ├── CanonVersion
  │   ├── TruthSource Binding
  │   └── Migration Plan
  │
  ├── LifeOps Transaction Layer
  │   ├── Validator
  │   ├── Transaction
  │   ├── LifeOps
  │   ├── CommitReceipt
  │   └── FinalGate
  │
  ├── Resource Layer
  │   ├── ResourceDefinition
  │   ├── ResourceAccount
  │   ├── ResourceLedger
  │   ├── ResourceReservation
  │   └── Reconcile
  │
  ├── Event / Schedule / Heartbeat Layer
  │   ├── Event
  │   ├── ScheduleBlock
  │   ├── WakeJob
  │   ├── Heartbeat
  │   └── Narrative Execution Simulator  ← v0.9
  │
  ├── Entity Resource Layer
  │   ├── InventoryItem
  │   ├── InventoryMovement
  │   └── MealRecord
  │
  ├── Long-term Life Layer
  │   ├── LifeArc
  │   ├── Goal
  │   ├── EventDecomposition
  │   ├── EventDependency
  │   └── Reflection
  │
  ├── Autonomy Layer
  │   ├── AutonomyDecision
  │   └── Proposed LifeOps
  │
  ├── Proactive Layer
  │   ├── ProactiveIntent
  │   ├── ProactiveEvaluation
  │   ├── Outbox
  │   └── AgentUserProactiveState
  │
  ├── Memory Layer
  │   ├── Structured Memory
  │   ├── FTS5
  │   ├── sqlite-vec
  │   └── Diary
  │
  └── Trace Layer
      ├── TraceRun
      ├── TraceSpan
      ├── LifeJournal hash chain
      ├── AuditLog
      └── Explain / Verify
```

---

## 3. Narrative Execution Simulator

v0.9 新增 `execution_decisions`。它是 heartbeat 执行每个 due `WakeJob` 时的第一步。

```text
WakeJob due
  ↓
claim wake job
  ↓
load ScheduleBlock + Event + Resources + TruthSources + Dependencies
  ↓
Execution Simulator records decision
  ↓
Decision proposes LifeOps
  ↓
LifeOps Validator
  ↓
Transaction Commit
  ↓
CommitReceipt / Journal / Trace
  ↓
finish wake job
```

### 3.1 ExecutionDecision 数据

```text
execution_decisions
  id
  owner_kind / owner_id
  tick_id
  trace_id
  wake_job_id
  schedule_block_id
  event_id
  decision_type
  status
  reason
  score_json
  proposed_ops_json
  result_transaction_id
  result_receipt_id
  error
  created_at
  committed_at
```

`decision_type` 当前包括：

```text
completed
partial
postponed
block_completed
skip_terminal
```

后续可以扩展：

```text
failed
cancelled
skipped
rescheduled
needs_user_input
requires_truth_source
```

---

## 4. 当前执行策略

v0.9 是保守、确定性的模拟器，不做高戏剧性随机生成。

### 4.1 依赖未完成

```text
if event has active dependencies and dependency event is not completed:
  decision_type = postponed
  old schedule block -> rescheduled
  event -> rescheduled
  create new schedule block +1 day
```

### 4.2 天气不适合

如果最近 weather truth source 表明 rain / storm / snow / windy，并且事件类型是：

```text
purchase / travel / social / health / fitness / walk / outdoor
```

且事件重要性不是极高，则：

```text
decision_type = postponed
reason = bad weather
old schedule block -> rescheduled
new schedule block +2 days
optional proactive intent
```

### 4.3 资源不足

如果 `resource_costs` 会让资源账户低于最小值：

```text
importance >= 75:
  decision_type = partial
  schedule block consumed
  event -> in_progress -> partial
  reflection created
  proactive intent created

importance < 75:
  decision_type = postponed
  event rescheduled
```

### 4.4 条件正常

```text
UPDATE_SCHEDULE_BLOCK_STATUS completed
COMPLETE_EVENT
CREATE_MEMORY for important events
optional CREATE_SERENDIPITY_EVENT
optional CREATE_PROACTIVE_INTENT for highly important events
```

---

## 5. Serendipity Engine

Serendipity 是“小发现 / 小问题 / 小意外”。它不是大剧情，也不是任意乱编，而是在重要事件完成后产生的低强度生活纹理。

新增表：

```text
serendipity_events
  id
  owner_kind / owner_id
  event_id
  trigger_event_id
  trigger_result_id
  serendipity_type
  title
  description
  intensity
  emotional_impact_json
  proposed_ops_json
  status
  trace_id
  created_at
```

每个 serendipity 同时会创建一个 completed `events` 记录：

```text
event_type = serendipity
source = serendipity
status = completed
```

这样它既是实体生活事件，又有 serendipity 专属解释表。

---

## 6. 新增 LifeOps

v0.9 新增：

```text
UPDATE_SCHEDULE_BLOCK_STATUS
CREATE_SERENDIPITY_EVENT
```

### 6.1 UPDATE_SCHEDULE_BLOCK_STATUS

用于把 schedule block 标记为：

```text
completed
skipped
cancelled
rescheduled
missed
```

这避免 heartbeat 直接改 schedule block，确保 schedule 状态变化也有 LifeOps / receipt / trace。

### 6.2 CREATE_SERENDIPITY_EVENT

用于创建小发现、小困难、小意外。它会：

```text
1. 创建 completed Event
2. 创建 serendipity_events row
3. 写 Journal
4. 写 CommitReceipt fact
```

---

## 7. 新增工具

```text
life_execution
```

支持：

```text
list / decisions
get
run
simulate
execute
serendipity
```

示例：

```json
life_execution({"action":"run", "schedule_block_id":"block_xxx"})
```

```json
life_execution({"action":"serendipity"})
```

---

## 8. Heartbeat v0.9 顺序

```text
Heartbeat tick
  ↓
TruthSource refresh
  ↓
Due WakeJobs
  ↓
Narrative Execution Simulator
  ↓
LifeOps commit for execution outcome
  ↓
Resource recovery
  ↓
Autonomy Planner
  ↓
Proactive Evaluator
  ↓
Journal / Trace
```

与 v0.8 相比，最重要的变化是：

```text
schedule_block_end 不再直接 COMPLETE_EVENT。
它必须先产生 execution_decision。
```

---

## 9. Trace / Explain

每次执行可以解释：

```text
为什么这个 event 完成 / 部分完成 / 推迟？
当时 weather truth 是什么？
资源是否足够？
有哪些依赖？
产生了哪些 LifeOps？
对应 transaction / receipt 是什么？
有没有生成 serendipity 或 proactive intent？
```

命令：

```text
/life execution list
/life execution serendipity
/life trace explain <transaction_id>
/life trace explain <event_id>
```

---

## 10. 当前闭环状态

截至 v0.9：

```text
Canon 设定闭环：已实现
Pause / setup 防污染闭环：已实现
LifeOps mutation 闭环：已实现
CommitReceipt / FinalGate 闭环：已实现
Scalar Resource Ledger 闭环：已实现
Entity Inventory / Meals 闭环：已实现
TruthSource resolve / observe / cache / trace 闭环：已实现
WakeJob Heartbeat 闭环：已实现
User Life Confirmation 闭环：已实现
Goals / Life Arcs / Event Decomposition 闭环：已实现
Reflection 复盘闭环：已实现
Autonomy Planner 决策与执行闭环：已实现
Proactive Intent / Outbox / State 闭环：已实现
Narrative Execution / Serendipity 闭环：已实现
Trace hash-chain verify：已实现
```

---

## 11. 下一步路线

v1.0 前还建议补：

```text
1. Stronger event lifecycle repair：失败 / 取消 / 跳过 / 重新规划更细状态机
2. Persona-weighted execution：性格参数影响拖延、冲动、谨慎、社交倾向
3. Location continuity：地点、移动时间、交通 buffer
4. Recurring routine generator：睡觉、吃饭、工作、日常洗漱
5. Full branch/fork：世界观重置时真正分支化所有 life state
6. Optional Hermes core mandatory final gate：形式化 fail-closed
```
