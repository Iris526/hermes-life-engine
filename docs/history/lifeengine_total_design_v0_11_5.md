# LifeEngine 总设计文档 v0.11.5 — 睡眠不足、通宵、延迟回复聚合与 DreamAudit 修复策略

```text
Plugin version: 0.11.5
Schema version: 25
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

v0.11.5 建立在 v0.11.0 Event V2、v0.11.1 SleepPlan/SleepSession、v0.11.2 ReplyGate、v0.11.3 DreamRun/DreamAudit/DreamEntry、v0.11.4 Sleep/Reply/Dream 验收与修复应用之上。本版不新增大型生活能力，而是让“睡得不好”真正影响 Agent 第二天的状态，并让延迟回复和 DreamAudit 修复策略更产品化。

## 1. 设计目标

v0.11.5 解决五个问题：

1. 睡眠不足不能只是 SleepSession 的一个结果字段，必须沉淀成次日可读的实时/日状态。
2. 通宵必须被显式识别，并影响 energy / focus / mood / fatigue / recovery pressure。
3. sleep debt 需要对午睡、补觉和 Autonomy 产生压力。
4. 延迟回复释放时不能只是把多条消息原样丢给 Agent，要形成可读摘要。
5. DreamAudit 修复不能只有“手动 repair”，还要有 policy：off / manual / auto_safe。

## 2. 新增数据表

### 2.1 sleep_day_states

`sleep_day_states` 是睡眠后果的 materialized day state。

核心字段：

```text
date_key
source_sleep_plan_id
source_sleep_session_id
planned_sleep_minutes
actual_sleep_minutes
sleep_debt_delta_minutes
cumulative_sleep_debt_minutes
all_nighter
energy_penalty
focus_penalty
mood_penalty
fatigue_delta
recovery_pressure
nap_recommended
recovery_plan_id
resource_ledger_ids_json
body_state_json
mind_state_json
```

它不替代 SleepSession。SleepSession 表示实际睡眠；SleepDayState 表示这次睡眠对接下来一天的身体和心智状态造成了什么影响。

### 2.2 sleep_recovery_plans

记录由 sleep debt / recovery pressure 触发的补觉或小憩计划。

```text
sleep_day_state_id
sleep_plan_id
reason
pressure
status
metadata_json
```

### 2.3 delayed_reply_digests

当 Agent 睡觉或不可打断事件中积累多条 delayed replies，释放时生成摘要：

```text
delayed_reply_ids_json
message_count
summary_text
release_reason
created_by
metadata_json
```

Agent 醒来后可以自然地说：

```text
我刚才不方便及时回复时收到了 2 条消息，主要是……
```

而不是机械地逐条重复。

### 2.4 dream_repair_policies

DreamAudit 修复策略：

```text
mode: off | manual | auto_safe
safe_finding_types_json
auto_apply_limit
updated_by
updated_at
```

含义：

```text
off:
  DreamAudit 只记录 findings，不收集 repair ops。

manual:
  默认策略。用户/Agent 显式调用 repair 才应用。

auto_safe:
  只收集安全类型 findings 的 proposed_ops，例如 stale_schedule_block、pending_delayed_replies、stale_resource_reservation。
```

## 3. 睡眠不足计算

`record_post_sleep_day_state()` 在 SleepSession wake 或 missed core sleep 时运行。

输入：

```text
planned_minutes
actual_minutes
previous_cumulative_debt
```

输出：

```text
sleep_debt_delta_minutes
cumulative_sleep_debt_minutes
all_nighter
energy_penalty
focus_penalty
mood_penalty
fatigue_delta
recovery_pressure
nap_recommended
```

规则是保守确定性的，不依赖 LLM。

## 4. 通宵策略

如果 core sleep 计划存在，但 wake 时没有实际 SleepSession，LifeEngine 记录 missed sleep plan 并创建 SleepDayState：

```text
actual_sleep_minutes = 0
all_nighter = true
nap_recommended = true
recovery_pressure 高
```

这让第二天的 Autonomy、Diary、Dream、ReplyGate 都可以知道：Agent 昨晚没有真正睡觉。

## 5. 资源结算

如果资源已在 ResourceRegistry 定义，LifeEngine 会写入 ledger：

```text
energy -= energy_penalty
focus -= focus_penalty
mood -= mood_penalty
fatigue += fatigue_delta
```

没有定义的资源不会被自动创建，避免污染资源体系。

## 6. 补觉压力

`life_sleep(action="recovery_plan")` 会读取最新 SleepDayState：

```text
recovery_pressure >= threshold
或 nap_recommended = true
```

则生成一个 soft `recovery_sleep` SleepPlan。它不是每日强制睡眠，而是由状态压力触发的临时补觉事件。

## 7. 延迟回复摘要

`release_delayed_replies()` 现在会自动创建 `delayed_reply_digests`。

释放结果中包含：

```text
released_count
replies
digest
```

这支持醒来后聚合回复。

## 8. DreamAudit 修复策略

`life_dream` 新增：

```text
repair_policy
set_repair_policy
```

`repair_plan` 会根据 policy 返回：

```text
policy_blocked=true  # off
ops=[]
```

或只返回 safe findings 的 ops：

```text
mode=auto_safe
```

真正应用 repair 仍然必须走 LifeOps transaction，不绕过 validator / journal / receipt。

## 9. 新增工具面

### life_sleep

新增 action：

```text
day_state
day_states
effects
record_effects
all_nighter
recovery_plan
plan_recovery
```

### life_reply

新增 action：

```text
digests
digest_list
```

### life_dream

新增 action：

```text
repair_policy
policy
set_repair_policy
policy_set
```

## 10. 测试覆盖

v0.11.5 新增测试覆盖：

1. schema version 25 和新增表存在。
2. 短睡眠会写 SleepDayState，并产生 recovery pressure。
3. recovery pressure 能生成 recovery_sleep 补觉计划。
4. missed core sleep 会记录 all_nighter。
5. delayed replies release 会创建 digest。
6. Dream repair policy=off 会阻断 repair ops。
7. Dream repair policy=auto_safe 会放行 safe repair ops。

## 11. 当前边界

v0.11.5 只是把睡眠后果、补觉压力、延迟回复聚合和 DreamAudit policy 落地。下一步如果继续推进，建议做：

```text
v0.11.6 Autonomy reads SleepDayState
v0.11.7 Dream share timing / wake sharing UX
v0.11.8 Sleep/Reply/Dream real Hermes gateway integration smoke
```
