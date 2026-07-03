# LifeEngine 总设计文档 v0.11.1 — SleepPlan / SleepSession Layer

> v0.11.1 是睡眠层的第一阶段升级。它建立在 v0.11.0 Event V2 之上，把“睡觉”从一个普通 schedule block 升级为 **SleepPlan + SleepSession + Event V2 + ScheduleBlock + Realtime State** 的组合模型。

```text
Plugin version: 0.11.1
Schema version: 21
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

---

## 1. 为什么需要 SleepPlan / SleepSession

v0.11.0 已经补上了 Event V2、显式状态流转和 Agent realtime state，但睡眠有一个特殊问题：

```text
计划睡眠 != 实际睡眠
```

Agent 可能计划 23:30 睡到 07:00，但实际会因为：

```text
一直和用户聊天
工作/学习延误
压力导致睡不着
闹钟叫醒
自然醒
用户消息打断
call override 强制唤醒
通宵
午睡/小憩补觉
```

所以睡眠不能只用一条 schedule block 表示。v0.11.1 把睡眠拆成：

```text
SleepPlan      今天打算怎么睡
SleepSession   实际什么时候睡、什么时候醒、睡了多久、为什么醒
Sleep Event    作为生活事件存在，用 Event V2 表达属性和生命周期
Sleep Block    在日程上占用时间
Realtime State 当前是否 asleep/napping，以及是否可被打断
```

---

## 2. 数据模型

### 2.1 `sleep_plans`

表示计划睡眠。

核心字段：

```text
id
owner_kind / owner_id
date
status
plan_type                  core_sleep / nap / recovery_sleep
event_id
schedule_block_id
planned_sleep_at / planned_sleep_at_ts
planned_wake_at / planned_wake_at_ts
planned_duration_minutes
timezone
alarm_at / alarm_at_ts
alarm_label
wake_policy                natural / alarm / natural_or_alarm / schedule
constraints_json
decision_json
canon_version
created_at / updated_at / completed_at
```

### 2.2 `sleep_sessions`

表示实际睡眠。

核心字段：

```text
id
owner_kind / owner_id
sleep_plan_id
event_id
schedule_block_id
session_type               core_sleep / nap / recovery_sleep
status                     asleep / completed / interrupted / missed / cancelled
actual_sleep_at / actual_sleep_at_ts
actual_wake_at / actual_wake_at_ts
actual_duration_minutes
planned_duration_minutes
wake_cause                 natural / alarm / user_interrupt / call_override / schedule / unknown
interrupted_by
quality_score
sleep_debt_delta_minutes
resource_effects_json
created_at / updated_at / completed_at
```

### 2.3 `sleep_interruptions`

记录用户消息、call override、系统事件等对睡眠的打断。

```text
id
owner_kind / owner_id
sleep_session_id
interrupted_at / interrupted_at_ts
source
reason
user_id
session_id / turn_id
caused_wake
metadata_json
```

### 2.4 `sleep_session_state_transitions`

显式记录睡眠 session 生命周期：

```text
None -> asleep
asleep -> completed
asleep -> interrupted
asleep -> missed
```

---

## 3. 与 Event V2 / Schedule 的关系

睡眠不是单独脱离生活系统的状态。创建 SleepPlan 时会同时创建：

```text
Event V2:
  event_category = sleep
  activity_domain = sleep
  event_type = core_sleep / nap / recovery_sleep
  interruptibility = sleep_interruptible

ScheduleBlock:
  block_type = sleep
  start / end = planned sleep / wake
  lock_strength = hard for core_sleep, soft for nap

WakeJobs:
  reason = sleep_plan_start
  reason = sleep_plan_wake
```

也就是说：

```text
SleepPlan 是计划层
SleepSession 是实际层
Event 是生活事实层
ScheduleBlock 是时间占用层
WakeJob 是 heartbeat 推进层
```

---

## 4. Heartbeat 行为

Heartbeat 现在能识别睡眠 wake jobs：

```text
sleep_plan_start
  ↓
START_SLEEP_SESSION
  ↓
RealtimeState.mode = asleep / napping
  ↓
Event -> in_progress
  ↓
ScheduleBlock -> in_progress

sleep_plan_wake
  ↓
WAKE_SLEEP_SESSION
  ↓
SleepSession completed/interrupted/missed
  ↓
Event completed/partial
  ↓
ScheduleBlock completed/missed
  ↓
RealtimeState.mode = idle
```

普通 `schedule_block_end` 不再直接处理 sleep block，避免重复完成睡眠。

---

## 5. Realtime State 集成

v0.11.1 扩展了 realtime state：

```text
agent_realtime_state.active_sleep_session_id
agent_state_snapshots.active_sleep_session_id
```

入睡时：

```text
mode = asleep / napping
active_event_id = sleep event
active_schedule_block_id = sleep schedule block
active_sleep_session_id = sleep session
interruptibility_level = sleep_interruptible
reply_mode = defer_or_wake
body_state.sleeping = true
```

醒来时：

```text
mode = idle
active_event_id = null
active_schedule_block_id = null
active_sleep_session_id = null
reply_mode = immediate
body_state.sleeping = false
body_state.last_sleep_duration_minutes = actual duration
```

这为 v0.11.2 ReplyGate / delayed replies / life_call 做准备。

---

## 6. 资源结算

睡眠可以影响资源，但仍然遵守 Resource Registry 原则：

```text
没有被登记的资源，不会自动创建。
```

如果 Agent 已定义：

```text
energy
sleep_debt
```

醒来时会写入 resource ledger：

```text
energy += estimated recovery
sleep_debt += planned_duration - actual_duration, if under-slept
```

如果没有定义这些资源，SleepSession 仍会记录：

```text
resource_effects_json
body_state_json
```

但不会污染资源账本。

---

## 7. 中断机制的第一阶段

v0.11.1 实现了睡眠中断记录：

```text
INTERRUPT_SLEEP_SESSION
```

如果 `caused_wake=true`：

```text
sleep interruption row
  ↓
wake sleep session with wake_cause=user_interrupt
  ↓
status = interrupted
  ↓
realtime state returns to idle
```

这不是完整 ReplyGate。完整的用户消息排队、不可打断事件、call override 和延迟回复将在 v0.11.2 实现。

---

## 8. 工具与命令

新增/增强工具：

```text
life_sleep
```

支持：

```text
status / state
plan / create_plan / ensure_daily
nap
start / sleep
wake / wake_up / end
interrupt / call_interrupt
plans / sessions
get_plan / get_session
doctor
```

示例：

```json
{
  "action": "plan",
  "planned_sleep_at": "2026-06-10T23:30:00+09:00",
  "planned_wake_at": "2026-06-11T07:00:00+09:00",
  "plan_type": "core_sleep",
  "wake_policy": "natural_or_alarm"
}
```

---

## 9. Doctor / Trace

Doctor 新增睡眠检查：

```text
stale_sleep_session
missed_sleep_plan
sleep_state mismatch
expired realtime-state lease
```

Trace explain event 现在可以串起：

```text
sleep plan
sleep session
sleep transitions
sleep interruptions
schedule transitions
realtime snapshots
resource ledger
```

---

## 10. 本版边界

v0.11.1 只完成睡眠底座，不做：

```text
完整 ReplyGate
不可打断事件回复排队
life_call 强制唤醒接口
DreamRun / DreamAudit / DreamEntry
醒来分享梦
```

推荐后续：

```text
v0.11.2 ReplyGate / delayed_replies / life_call
v0.11.3 DreamRun / DreamAudit / DreamEntry
```

---

## 11. 测试覆盖

v0.11.1 新增测试覆盖：

```text
schema version 21
sleep tables exist
SleepPlan 创建 Event V2 + ScheduleBlock + wake jobs
SleepSession start/wake 实际时间与状态
energy / sleep_debt registered resource settlement
heartbeat start/wake sleep plan
sleep interruption and interruption history
realtime state active_sleep_session_id snapshots
```

定向睡眠测试：

```text
9 passed
```

分文件完整回归：

```text
69 passed
```

说明：全量单命令在当前测试容器里会因为长维护/heartbeat smoke 组合运行超时；同一批测试按文件拆分后全部通过，且失败退出码均为 0。
