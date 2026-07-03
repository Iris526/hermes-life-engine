# LifeEngine 总设计文档 v0.7

## 0. 版本定位

v0.7 在 v0.6 的长期目标、Life Arc、事件拆解和复盘闭环之上，加入第一版
**Autonomy Planner**。它的目标不是让 Agent 随机生成生活，而是让 Agent 在
Heartbeat 或显式 `/life autonomy run` 中，基于已提交的 Canon、Goal、Resource、
Schedule、TruthSource 和模块开关，提出小而可审计的自主行动。

核心原则保持不变：

```text
模型可以提出生活，但生活事实必须通过 LifeEngine。
Autonomy 可以提出行动，但行动必须通过 LifeOps。
Heartbeat 可以推进生活，但推进结果必须可追踪、可回放、可解释。
```

## 1. v0.7 新增概念

### 1.1 Autonomy Planner

Autonomy Planner 是 LifeEngine 的自治规划器。它读取：

```text
Life Canon
Module Gates
Active Goals / Life Arcs
Open Events / Schedule
Resource Accounts / Reservations
TruthSource cache / recent reads
Recent autonomy decisions
Current heartbeat tick
```

输出不是直接数据库写入，而是：

```text
AutonomyDecision
  └── proposed LifeOps
```

然后沿统一闭环执行：

```text
AutonomyDecision
  ↓
LifeOps
  ↓
Validator
  ↓
Transaction
  ↓
Journal / Trace
  ↓
CommitReceipt
  ↓
FinalGate evidence
```

### 1.2 AutonomyDecision

每一次自治规划，无论执行、跳过、失败，都必须登记为 decision。

```text
autonomy_decisions:
  id
  owner_kind / owner_id
  tick_id
  trace_id
  mode
  status: proposed / committed / skipped / rejected / error
  reason
  selected_goal_id
  selected_event_id
  score_json
  proposed_ops_json
  result_transaction_id
  result_receipt_id
  error
```

候选项可写入：

```text
autonomy_candidates:
  decision_id
  candidate_type
  title
  score
  payload_json
  status
```

### 1.3 自治模块开关

Autonomy 受 Control Plane 的 module gate 控制：

```text
autonomy = off
  不做自治规划。

autonomy = manual
  只响应 /life autonomy run 或 life_autonomy({action:"run"})。

autonomy = planned_only
  只推进已有计划，不主动创造新目标事件。

autonomy = low_spontaneity
  Heartbeat 可创建低频、小型、目标相关行动。

autonomy = full
  Heartbeat 可更积极地围绕目标、资源、复盘、主动意图生成候选行动。
```

v0.7 实现的是 deterministic planner。LLM 型叙事规划留给后续版本。

## 2. v0.7 自治行为策略

### 2.1 Goal-aware next step

当存在 active goal，且该 goal 没有未完成的 linked event，Planner 可以提出：

```text
CREATE_EVENT:
  title = 推进目标：<goal title>
  source = autonomy
  status = planned
  goal_id = <goal id>
```

提交后，事件会自动链接回 goal：

```text
Event
  ↓
EventGoalLink
  ↓
GoalProgress / future recompute
```

这让 Agent 可以围绕长期目标自发创建下一步，而不是每次等待用户问。

### 2.2 Resource-aware recovery

如果资源显示 Agent 状态过低，例如：

```text
energy < 15
```

Planner 不应该强行推进高优先级目标，而应优先提出：

```text
CREATE_EVENT:
  title = 休息并恢复精力
  event_type = rest
  source = autonomy
  status = planned
```

这让自主行为受资源闭环约束，避免“24 小时永动机 Agent”。

### 2.3 Heartbeat integration

Heartbeat 流程扩展为：

```text
Heartbeat tick
  ↓
读取 ControlPlane / Canon / TruthSource / Resource / Goals
  ↓
执行 due wake jobs
  ↓
如果 autonomy gate 允许：Autonomy Planner 生成 decision
  ↓
如果 decision 有 proposed_ops：统一 LifeOps commit
  ↓
写入 autonomy_decisions.result_transaction_id / receipt_id
  ↓
返回 tick.autonomy
```

### 2.4 Manual run

显式触发：

```text
/life autonomy run
life_autonomy({"action":"run"})
hermes lifeengine autonomy run
```

即使 heartbeat 关闭，也可以手动运行一次自治规划。

## 3. v0.7 闭环

### 3.1 自治规划闭环

```text
Canon + Goals + Resources + Schedule + TruthSource
  ↓
AutonomyDecision
  ↓
LifeOps proposal
  ↓
Validator
  ↓
Transaction commit
  ↓
CommitReceipt facts
  ↓
FinalGate evidence
```

### 3.2 Trace 闭环

每次 autonomy run/tick 会留下：

```text
trace_run
trace_span
life_transaction
life_ops
autonomy_decision
commit_receipt
life_journal hash chain
```

`/life trace explain <tx_id>` 可以看到 autonomous action 的 receipt facts。

### 3.3 防污染规则

```text
paused / paused_setup / read_only / disabled:
  不运行 autonomy。

setup / paused_setup:
  只写 CanonDraft，不写 autonomy events。

User Life:
  Autonomy 不替用户生成真实生活事实。

所有 autonomy mutation:
  必须走 LifeOps。
```

## 4. 当前已实现能力总览

截至 v0.7：

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
Trace hash-chain verify：已实现
```

## 5. v0.7 仍然刻意不做的事

```text
1. 不让 LLM 在后台自由编生活。
2. 不做复杂 Serendipity 随机事件。
3. 不默认自动主动推送消息。
4. 不替用户生活自动执行计划。
5. 不绕过 LifeOps 写状态。
```

这些会留到后续版本，在当前 deterministic autonomy 稳定后逐步打开。

## 6. 后续建议 v0.8

v0.8 建议进入：

```text
Proactive Outbox / Delivery Policy / Relationship-aware sharing
```

也就是把 Agent 在自治行动中产生的“想说的话”变成完整状态机：

```text
Life Event / Autonomy Result / Reflection
  ↓
ProactiveIntent
  ↓
Delivery Policy
  ↓
Outbox
  ↓
sent / pending / suppressed / expired
  ↓
User response updates relationship state
```

在 v0.7 前不急着做主动推送，是因为必须先确保 Agent 的自主行动本身可解释、可审计、可追踪。
