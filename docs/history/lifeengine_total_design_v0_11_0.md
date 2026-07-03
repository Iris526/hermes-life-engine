# LifeEngine 总设计文档 v0.11.0 — Event V2 Upgrade

> v0.11.0 是 Event V2 升级包。它不引入完整 Sleep / Dream / ReplyGate 执行器，而是先把事件、日程、动作的生命周期追踪和 Agent 实时状态锚点打牢。

## 1. 版本与 schema

```text
Plugin version: 0.11.0
Schema version: 20
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 2. 为什么需要 Event V2

v0.10.0 的 Event 已经能表达 `planned_start`、`actual_end`、`status`、`resource_costs` 和 `schedule_block_ids`，但它仍偏向“当前状态行”。要支持睡眠、梦、不可打断事件、延迟回复、真实生活节奏和完整追踪，需要三类升级：

1. **事件分类与属性**：休闲、工作、学习、睡眠、梦、维护、旅行等应该结构化，而不是全塞进 title。
2. **状态流转显式化**：不能只从 journal 倒推，要能直接查 planned → scheduled → in_progress → partial/completed。
3. **实时状态锚点**：Agent 当前是否忙、是否睡觉、是否可打断、是否需要延迟回复，必须是第一等状态。

## 3. Event V2 字段

v0.11.0 给 `events` 增加：

```text
event_category
activity_domain
subtype
tags_json
attributes_json
location_json
participants_json
interruptibility_json
state_effects_json
current_schedule_block_id
actual_duration_minutes
last_transition_id
lifecycle_version
```

推荐 `event_category`：

```text
sleep, work, study, health, meal, purchase, social, leisure,
maintenance, travel, creative, finance, relationship, reflection,
dream, system, other
```

示例：

```json
{
  "event_category": "work",
  "activity_domain": "craft_commission",
  "event_type": "repair_task",
  "subtype": "rain_shelter_node_review",
  "tags": ["commission", "minor_job"],
  "interruptibility": {
    "level": "soft_interruptible",
    "max_delay_minutes": 30,
    "call_override_allowed": true
  }
}
```

## 4. Schedule 仍然不是 Event

v0.11.0 继续保持：

```text
Event = 生活中的事情
ScheduleBlock = 这件事占用的时间块
Action = 这件事内部发生的动作
Result = 动作/事件造成的结果
```

一个 Event 可以有多个 ScheduleBlock；ScheduleBlock 通常指向 Event，但也可以是 buffer/free/system block。

ScheduleBlock 新增：

```text
actual_start / actual_end
actual_start_ts / actual_end_ts
planned_duration_minutes
actual_duration_minutes
interruptibility_json
transition_reason
last_transition_id
```

这为“计划睡眠和实际睡眠不一致”“用户打断睡眠”“不可打断事件延迟回复”打基础。

## 5. 显式状态流转表

新增：

```text
event_state_transitions
schedule_block_state_transitions
action_state_transitions
```

每条状态变化记录：

```text
from_status
to_status
reason
source
transaction_id / op_id / receipt_id
schedule_block_id / action_id / result_id
occurred_at / occurred_at_ts
metadata_json
trace_id
```

这样可以回答：

```text
这个事件什么时候创建？
什么时候排进日程？
什么时候开始？
什么时候完成？
为什么推迟？
哪个 action/result 导致完成？
```

## 6. Agent 实时状态锚点

新增：

```text
agent_realtime_state
agent_state_snapshots
```

`agent_realtime_state` 是当前 materialized state：

```text
mode: awake / idle / busy / in_conversation / asleep / napping / dreaming /
      uninterruptible_event / waiting_to_reply / recovering / system_paused
active_event_id
active_action_id
active_schedule_block_id
active_sleep_session_id
interruptibility_level
reply_mode
lease_expires_at / lease_expires_at_ts
body_state_json
mind_state_json
environment_state_json
```

它不替代 Resource Ledger，而是用来做实时决策：能不能回复、能不能被打断、是否处于不可打断事件、是否有过期租约。

## 7. 新增 LifeOps / 工具行为

新增 LifeOp：

```text
UPDATE_REALTIME_STATE
```

`life_event` 新增 action：

```text
get
transitions
schedule_transitions
state
update_state
```

所有 durable mutation 仍然走：

```text
LifeOps → Validator → Transaction → Journal → CommitReceipt → FinalGate evidence
```

## 8. Trace / Doctor 升级

`life_trace explain <event_id>` 现在包含：

```text
event_state_transitions
schedule_state_transitions
action_state_transitions
state_snapshots
```

Doctor 新增：

```text
event_transition_coverage
realtime_state_lease
```

可以发现：

```text
事件没有 transition history
Agent 卡在过期不可打断/睡眠/等待回复 lease
```

## 9. 和 Sleep / Dream / ReplyGate 的关系

v0.11.0 不直接实现完整睡眠/梦/回复门禁，但它提供这些系统需要的底座：

```text
SleepPlan / SleepSession 可以挂到 Event V2 + ScheduleBlock V2
DreamRun 可以基于 SleepSession 触发并写 dream event / reflection / proactive intent
ReplyGate 可以读取 agent_realtime_state 决定即时回复、延迟、call override
不可打断事件可以使用 interruptibility_json + lease_expires_at 防死锁
```

后续建议：

```text
v0.11.1 SleepPlan / SleepSession
v0.11.2 ReplyGate / delayed_replies / life_call
v0.11.3 DreamRun / DreamAudit / DreamEntry
```

## 10. 验收重点

v0.11.0 新增测试覆盖：

```text
1. schema version 20 与 Event V2 表存在。
2. Event V2 属性可创建、读取、receipt 化。
3. Event 创建、排期、完成均写 transition history。
4. ScheduleBlock 创建、完成均写 transition history。
5. Action 完成写 action_state_transitions。
6. realtime state 可更新并写 snapshot。
7. doctor 能发现过期 realtime-state lease。
8. trace explain event 返回完整 transition context。
```
