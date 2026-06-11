# LifeEngine 总设计文档 v0.11.6 — Autonomy 读取 SleepDayState 与睡眠感知自治规划

Plugin version: `0.11.6`  
Schema version: `26`  
sqlite-vec: required

## 1. 本版定位

v0.11.6 建立在 v0.11.5 的 SleepDayState / sleep debt / all-nighter / recovery pressure 基础上。这一版不新增大型子系统，而是让 **Autonomy Planner 真正读取 Agent 的睡眠后果、实时疲劳、睡眠债和专注力惩罚**，避免 Agent 在睡眠不足、通宵或疲劳过高时仍然像永动机一样推进工作/学习目标。

核心目标：

```text
SleepSession / SleepDayState
  ↓
Realtime body/mind state
  ↓
Autonomy Planner
  ↓
recovery sleep / light goal step / low-intensity recovery
  ↓
LifeOps → Validator → Transaction → Receipt → Trace
```

## 2. 设计原则

### 2.1 Autonomy is body-aware

自治规划不能只看 Goal 和资源余额，还必须看 Agent 当前身体/精神状态：

```text
sleep debt
all-nighter
fatigue
focus penalty
mood penalty
nap recommendation
recovery pressure
```

### 2.2 Recovery before productivity

当睡眠债或疲劳达到阈值时，Autonomy 必须优先安排恢复：

```text
补觉 / 小憩 / 低强度恢复
```

而不是强推：

```text
学习
工作
创作
高强度健康任务
```

### 2.3 Downshift, don't freeze

睡眠不好不等于 Agent 不能生活。它应该降低强度：

```text
高强度学习 → 轻量复盘
创作项目 → 整理想法
工作推进 → 低强度检查
健身训练 → 休息/拉伸
```

### 2.4 Still LifeOps-only

Autonomy 不直接写状态。睡眠感知调整也必须走：

```text
proposed ops
  ↓
Validator
  ↓
Transaction
  ↓
CommitReceipt
  ↓
Journal / Trace
```

## 3. 新增表

### autonomy_sleep_adjustments

记录 Autonomy 因睡眠状态而调整决策的原因。

```text
id
owner_kind
owner_id
decision_id
sleep_day_state_id
adjustment_type
severity
reason
sleep_context_json
proposed_ops_json
created_at
```

典型 adjustment_type：

```text
recovery_sleep_planned
all_nighter_downshift
goal_step_downshifted
existing_goal_reminder_downshifted
low_energy_sleep_aware_recovery
```

## 4. Autonomy Sleep Context

新增 `life_autonomy(action="sleep_context")`。

它返回：

```text
latest sleep_day_state
realtime mode
recovery_pressure
sleep_debt_minutes
fatigue
focus_penalty
mood_penalty
all_nighter
nap_recommended
existing_recovery_plan
severity
should_recover
should_downshift
```

severity：

```text
ok
mild
moderate
severe
```

## 5. 决策规则

### 5.1 高恢复压力 / 睡眠债 / fatigue

如果：

```text
all_nighter = true
或 recovery_pressure >= 70
或 nap_recommended = true
或 fatigue >= 65
```

且当天没有 recovery_sleep plan，则 Autonomy 创建：

```text
CREATE_SLEEP_PLAN
  plan_type = recovery_sleep
  title = 睡眠不足后的补觉 / 小憩
```

### 5.2 已有 recovery_sleep plan 但通宵

如果已经有补觉计划，同时状态仍然显示 all-nighter，Autonomy 会创建：

```text
通宵后的低强度恢复安排
```

而不是高强度目标事件。

### 5.3 睡眠状态一般但仍可推进目标

如果：

```text
should_downshift = true
```

且目标类型是：

```text
study / work / creative
```

则 Autonomy 创建：

```text
轻量推进目标：<goal title>
```

并降低资源成本：

```text
energy -3
focus -3
```

而不是正常强度的：

```text
energy -8
focus -10
```

### 5.4 Existing goal event reminder

如果目标已有 pending event，Autonomy 不重复创建目标事件。若睡眠状态不佳，它会把 self-journal proactive intent 的内容调整为：

```text
我还记得要推进这个目标，不过今天睡眠状态一般，我会把强度放轻。
```

## 6. Runtime / Tool 更新

### life_autonomy

新增 action：

```text
sleep_context
sleep_adjustments
```

CLI：

```bash
hermes lifeengine autonomy sleep_context
hermes lifeengine autonomy sleep_adjustments
```

Slash：

```text
/life autonomy sleep_context
/life autonomy sleep_adjustments
```

## 7. Context 注入

`recent_autonomy_decisions` 中现在包含 decision score 里的 sleep context 摘要。Agent 在下一轮对话中能看到：

```text
最近一次自治决策是否因睡眠债而改成补觉
是否因通宵而降低强度
是否因为 focus penalty 改成轻量目标
```

协议提示也更新为：

```text
Autonomy is sleep-aware: if SleepDayState shows all-nighter, high sleep debt, fatigue, or focus penalty, prefer recovery sleep, light work, postponement, or low-intensity goal steps.
```

## 8. Trace / Audit

每次睡眠感知调整写入：

```text
autonomy_sleep_adjustments
life_journal: autonomy_sleep_adjustment_recorded
autonomy_decisions.score.sleep
```

这样可以解释：

```text
为什么今天没有继续复习？
为什么改成补觉？
为什么只做轻量推进？
```

## 9. 测试覆盖

v0.11.6 新增测试覆盖：

```text
1. schema version 26 与 autonomy_sleep_adjustments 表存在。
2. 高 sleep debt / recovery pressure 时，Autonomy 优先创建 recovery_sleep plan。
3. 已存在 recovery_sleep plan 时，Autonomy 不重复补觉，而是把 goal step downshift 成轻量事件。
4. life_autonomy sleep_context 返回最新 SleepDayState 与 should_recover / should_downshift 判定。
```

完整回归：

```text
91 passed
```

## 10. 当前闭环状态

截至 v0.11.6：

```text
Event V2：已实现
Realtime State：已实现
SleepPlan / SleepSession：已实现
ReplyGate / delayed replies / life_call：已实现
DreamRun / DreamAudit / DreamEntry：已实现
DreamAudit repair：已实现
SleepDayState / all-nighter / recovery pressure：已实现
Autonomy reads SleepDayState：已实现
Autonomy sleep-aware downshift：已实现
```

## 11. 下一步建议

v0.11.7 建议做：

```text
Execution Simulator reads SleepDayState
```

也就是执行层也要受疲劳/睡眠债影响：同一个 scheduled event 到点时，睡眠状态会影响它是 completed、partial、postponed、skipped，还是转为轻量执行。
