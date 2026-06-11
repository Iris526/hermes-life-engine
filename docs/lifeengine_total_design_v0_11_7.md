# LifeEngine 总设计文档 v0.11.7 — Execution Simulator 读取 SleepDayState 与睡眠感知执行

Plugin version: `0.11.7`  
Schema version: `27`  
sqlite-vec: required

## 1. 本版定位

v0.11.7 建立在 v0.11.5 的 SleepDayState 和 v0.11.6 的 sleep-aware Autonomy 之上。这一版不新增新的生活大模块，而是让 **Narrative Execution Simulator** 在事件真正到点执行时读取 Agent 的睡眠后果、实时疲劳、睡眠债、专注力惩罚和通宵状态。

此前：

```text
SleepDayState → Autonomy Planner
```

现在补上：

```text
SleepDayState / realtime body state
  ↓
Execution Simulator
  ↓
completed / partial / postponed / skipped
  ↓
LifeOps → Validator → Transaction → Receipt → Trace
```

这样“睡得不好”不仅影响 Agent 自己安排什么，也会影响已经排好的事情是否能按计划完成。

## 2. 设计原则

### 2.1 执行不是默认成功

到点的 schedule block 不应该总是：

```text
scheduled → completed
```

它应该参考：

```text
依赖是否完成
天气是否适合
资源是否足够
睡眠债是否过高
疲劳是否过高
是否通宵
事件重要性
事件是否可低强度执行
```

### 2.2 睡眠不足影响“实际执行”，不只是“计划生成”

Autonomy 可以在白天决定安排补觉或轻量目标，但已有 schedule 仍然可能到点。Execution Simulator 必须在到点时再次判断：

```text
能不能做？
做完吗？
只能做一部分吗？
应该推迟吗？
```

### 2.3 重要事件可以低强度执行

睡眠不足不应该让 Agent 完全冻结。

```text
低重要性工作：推迟
高重要性工作：低强度部分执行
睡眠/恢复类事件：不受睡眠压力阻断
```

### 2.4 Sleep-aware execution 也必须可追踪

每一次因为睡眠状态改变执行结果，都必须写：

```text
execution_sleep_adjustments
execution_decisions
life_journal
CommitReceipt
Trace explain
```

## 3. 新增表

### execution_sleep_adjustments

记录 Execution Simulator 因睡眠状态而调整事件执行结果的原因。

```text
id
owner_kind
owner_id
execution_decision_id
sleep_day_state_id
event_id
schedule_block_id
adjustment_type
severity
reason
sleep_context_json
original_decision_type
adjusted_decision_type
proposed_ops_json
created_at
```

典型 adjustment_type：

```text
sleep_pressure_postponed
sleep_pressure_downshifted
```

典型 adjusted_decision_type：

```text
postponed
partial
```

## 4. Execution Sleep Context

新增：

```text
life_execution(action="sleep_context")
```

返回：

```text
latest SleepDayState
sleep_day_state_id
date_key
realtime_mode
sleep_debt_minutes
recovery_pressure
fatigue
focus_penalty
mood_penalty
all_nighter
nap_recommended
severity
should_postpone
should_downshift
```

Severity 判定：

```text
severe:
  all_nighter=true
  or recovery_pressure >= 85
  or fatigue >= 80

moderate:
  recovery_pressure >= 60
  or nap_recommended=true
  or fatigue >= 55
  or focus_penalty >= 30

mild:
  sleep_debt >= 90
  or fatigue >= 35
  or focus_penalty >= 15
```

## 5. 执行策略

### 5.1 睡眠豁免事件

以下事件不因睡眠压力推迟：

```text
sleep
core_sleep
nap
recovery_sleep
dream
meal
reflection
serendipity
rest
```

### 5.2 睡眠敏感事件

以下事件会读取 SleepDayState：

```text
work
study
creative
fitness
health
purchase
travel
social
maintenance
fieldwork
repair_task
```

如果事件没有明确分类，但有 `resource_costs` 或 priority 足够高，也会被视为睡眠敏感。

### 5.3 严重睡眠压力 + 低重要性事件

```text
all_nighter / severe fatigue / high recovery pressure
importance < 82
  ↓
postponed
```

LifeOps：

```text
UPDATE_SCHEDULE_BLOCK_STATUS → rescheduled
UPDATE_EVENT_STATUS → rescheduled
CREATE_SCHEDULE_BLOCK → next day
optional CREATE_PROACTIVE_INTENT
```

### 5.4 中高重要性事件

```text
sleep pressure exists
importance >= 82
  ↓
partial / low-intensity execution
```

LifeOps：

```text
UPDATE_SCHEDULE_BLOCK_STATUS → completed
UPDATE_EVENT_STATUS → in_progress
UPDATE_EVENT_STATUS → partial
CREATE_REFLECTION
```

也就是说，时间块过去了，但事件没有被完整完成，只留下低强度尝试和复盘。

## 6. 新增工具能力

### life_execution

新增 action：

```text
sleep_context
sleep_adjustments
```

示例：

```json
{"action": "sleep_context"}
```

```json
{"action": "sleep_adjustments", "limit": 20}
```

CLI：

```bash
hermes lifeengine execution sleep_context
hermes lifeengine execution sleep_adjustments
```

Slash：

```text
/life execution sleep_context
/life execution sleep_adjustments
```

## 7. Trace / Explain

`life_trace explain <event_id>` 已经包含 execution decisions。v0.11.7 中，execution decision 会带 sleep context，并且 `execution_sleep_adjustments` 可以追踪：

```text
这次执行为什么没有完成？
当时 SleepDayState 是什么？
是否通宵？
recovery_pressure 是多少？
最终 proposed LifeOps 是什么？
哪些 LifeOps 被提交？
```

## 8. 当前闭环

```text
SleepSession / missed core sleep
  ↓
SleepDayState
  ↓
Autonomy Planner 影响计划生成
  ↓
Execution Simulator 影响实际执行
  ↓
Event/Schedule 状态流转
  ↓
Reflection / Proactive / Trace
```

这让“睡得不好”形成完整生活后果，而不是单独存在的状态记录。

## 9. 测试覆盖

v0.11.7 新增测试：

```text
1. schema version 27 与 execution_sleep_adjustments 表存在。
2. 高 sleep debt 时，重要工作事件到点只能 partial。
3. all-nighter 后，低重要性工作事件到点被 postponed。
4. life_execution sleep_context 暴露最新 SleepDayState 与 should_downshift。
```

分文件回归合计：`95 passed`。

## 10. 下一步建议

v0.11.8 建议做：

```text
Sleep/Execution/Autonomy 的端到端场景验收：
- 通宵后第二天自动安排补觉
- 已排工作到点被降级 partial
- 低重要性任务被推迟
- 午睡后恢复，下午任务恢复正常
- 用户 call 打断睡眠后影响第二天执行
```

目标是把睡眠、自治、执行、回复和梦连成可验收的长场景。
