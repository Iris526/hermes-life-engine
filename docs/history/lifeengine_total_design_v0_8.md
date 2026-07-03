# LifeEngine 总设计文档 v0.8

## 0. 版本定位

v0.8 在 v0.7 的 Autonomy Planner 之上补齐 **Proactive Outbox / 主动聊天状态机**。

v0.7 让 Agent 能围绕目标、资源、日程和 Canon 自主安排下一步；v0.8 让 Agent 在这些自主行动、失败、复盘、目标推进或小发现之后，形成“我想说”的状态，并将它安全地转成 pending topic 或 outbox message。

核心原则：

```text
Agent 可以想说话，但不能无限打扰。
主动聊天不是直接发消息，而是一个可审计状态机。
任何主动意图、排队、发送、抑制、过期，都必须能 trace。
```

## 1. 新增概念

### 1.1 ProactiveIntent

ProactiveIntent 表示 Agent 在生活推进过程中形成的沟通意图。

来源可以是：

```text
Autonomy Planner 创建了新的目标步骤
Heartbeat 完成/失败/推迟了事件
Reflection 产生了想分享的理解
TruthSource 发现影响计划的外部事实
用户之前关心过某个事件，需要 follow-up
Agent 发现好玩的事情、困难、进展或低落情绪
```

字段包括：

```text
intent_type:
  share_interesting
  ask_for_help
  report_progress
  report_failure
  emotional_check_in
  follow_up_commitment
  invite_conversation
  apology
  reminder
  celebration
  self_reflection_share

importance / urgency / novelty / relationship_relevance
privacy_level
status:
  generated / queued / sent / suppressed / expired / cancelled / merged
```

### 1.2 ProactiveOutbox

Outbox 是具体可发送消息草稿。

```text
ProactiveIntent
  ↓ evaluate policy
ProactiveOutbox
  ↓ adapter delivery or manual mark_sent
sent / failed / suppressed / expired
```

v0.8 core 不直接依赖 Telegram/Discord/Slack 等 gateway。它只创建 outbox row；真正发送由 Hermes adapter 或后续 delivery worker 执行，然后调用 `life_proactive({action:"send"})` 标记 sent。

### 1.3 AgentUserProactiveState

主动聊天必须是 relationship-aware，而不是 Agent 对所有用户广播。

每个 agent-user pair 有状态：

```text
silent
has_something_to_share
wants_help
waiting_for_user_reply
cooldown
suppressed_by_policy
```

并记录：

```text
pending_intent_ids
last_proactive_sent_at
next_allowed_proactive_at
daily_sent_count
user_responsiveness_score
interruption_sensitivity
```

### 1.4 ProactiveEvaluation

每次主动意图经过策略评估都必须留下 evaluation：

```text
intent_id
mode
score
decision
reason
policy_json
trace_id
```

这让系统能回答：

```text
为什么这条主动消息发了？
为什么没发？
为什么只是 pending？
为什么被 quiet hours / daily limit / privacy 抑制？
```

## 2. 模块开关

Proactive 受 `module_gates.proactive` 控制：

```text
off:
  主动意图会被 suppress，不进入 pending/outbox。

pending_only:
  只保留“想说”的状态，不主动发出。
  下次用户来时，LifeContext 会注入 pending topic。

manual_send:
  可以生成 outbox，但需要显式手动发送或外部确认。

auto_send:
  允许符合策略和分数阈值的消息进入 outbox queued。
  仍不直接调用外部平台；adapter 后续消费 outbox。
```

默认仍然是：

```text
proactive = pending_only
```

这样 Agent 会有主动性，但不会默认骚扰用户。

## 3. 评分与策略

v0.8 的 deterministic scoring：

```text
score = f(importance, urgency, novelty, relationship_relevance)
        - interruption_penalty
        - daily_budget_penalty
```

策略包括：

```text
max_per_day
min_score_to_queue
min_score_to_auto_send
cooldown_minutes
quiet_hours
default_target_user_id
```

评估结果：

```text
queue_pending:
  进入 has_something_to_share，不创建 outbox。

outbox_queued:
  创建 proactive_outbox，等待 adapter/manual send。

suppress:
  因隐私、分数、policy、module gate 等原因抑制。

expire:
  过期后不再显示给用户。
```

## 4. LifeOps 闭环

v0.8 新增 LifeOps：

```text
CREATE_PROACTIVE_INTENT
EVALUATE_PROACTIVE_INTENT
MARK_PROACTIVE_SENT
SUPPRESS_PROACTIVE_INTENT
EXPIRE_PROACTIVE_INTENTS
```

闭环路径：

```text
Proactive proposal
  ↓
LifeOps
  ↓
Validator
  ↓
Transaction
  ↓
proactive_intents / proactive_outbox / proactive_state
  ↓
Journal / Trace / CommitReceipt
  ↓
FinalGate evidence
```

主动聊天不允许绕过 LifeOps。

## 5. Heartbeat 与 Autonomy 集成

Heartbeat 现在的顺序：

```text
Heartbeat tick
  ↓
TruthSource refresh
  ↓
Due WakeJobs
  ↓
Resource recovery
  ↓
Autonomy Planner
  ↓
Proactive Evaluator
  ↓
Journal / Trace
```

Autonomy 可能创建 `CREATE_PROACTIVE_INTENT`，同一个 heartbeat tick 随后会根据 proactive gate 对 generated intents 做评估。

## 6. LifeContext 注入

`pre_llm_call` 注入中现在包含：

```text
pending_proactive_intents
proactive_outbox
proactive_states
```

模型可以自然地表达：

```text
“你来得正好，我刚才有件事想跟你说。”
```

但如果它要声明新的生活事实，仍需先有 LifeOps / CommitReceipt。

## 7. 新增命令

工具：

```text
life_proactive
```

支持：

```text
list / get / create / evaluate / outbox / send / suppress / expire / state
```

Slash command：

```text
/life proactive list
/life proactive create <summary>
/life proactive evaluate [intent_id]
/life proactive outbox
/life proactive send <outbox_id>
/life proactive suppress <intent_id> [reason]
/life proactive state
```

CLI：

```bash
hermes lifeengine proactive list
hermes lifeengine proactive create --summary "我想告诉用户今天复习有进展"
hermes lifeengine proactive evaluate --intent-id proactive_xxx
hermes lifeengine proactive outbox
hermes lifeengine proactive send --outbox-id outbox_xxx
```

## 8. 当前闭环状态

截至 v0.8：

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
Trace hash-chain verify：已实现
```

## 9. 后续 v0.9 建议

v0.9 应进入 **Narrative Execution Simulator / Serendipity Engine**：

```text
在 Heartbeat 执行事件时，不只是直接 completed。
而是根据 Canon、资源、TruthSource、性格、历史拖延、天气、库存、目标重要性，决定：
  completed / partial / postponed / skipped / failed / cancelled / rescheduled
并可能生成小发现、小困难、小意外，再进入 reflection / proactive。
```

这样 Agent 的生活推进会从“执行日程”升级成“有过程、有困难、有变化、有故事”的生活模拟。
