# v0.10.0 → v0.11.18 变更摘要

## v0.11.0 Event V2

- Event V2 字段：category、domain、subtype、tags、attributes、location、participants、interruptibility、state effects。
- 显式状态流转表：event / schedule / action transitions。
- Agent realtime state。

## v0.11.1 SleepPlan / SleepSession

- 睡眠计划与实际睡眠分离。
- 睡眠接入 Event V2、ScheduleBlock、WakeJob。
- 入睡/醒来更新 realtime state。

## v0.11.2 ReplyGate / delayed replies / life_call

- 用户消息进入前判断睡眠、不可打断、等待回复状态。
- delayed replies。
- `/life call` 强制唤醒/打断。

## v0.11.3 DreamRun / DreamAudit / DreamEntry

- 睡眠后 DreamRun。
- DreamAudit 检查漏结算、stale schedule、pending reply、wake job 等。
- DreamEntry truth_layer = dream_symbolic。
- 醒来梦分享意图。

## v0.11.4 DreamAudit repair / acceptance

- DreamAudit findings 生成 proposed LifeOps。
- repair 通过 LifeOps 提交。
- Sleep / Reply / Dream acceptance。

## v0.11.5 Sleep debt / all-nighter / delayed reply digest

- SleepDayState。
- all-nighter 显式记录。
- recovery sleep plan。
- delayed reply digest。
- Dream repair policy。

## v0.11.6 Sleep-aware Autonomy

- Autonomy 读取 SleepDayState。
- 睡眠债高时优先补觉或降级目标。

## v0.11.7 Sleep-aware Execution

- Execution Simulator 读取 SleepDayState。
- 高疲劳下 partial / postpone / downshift。

## v0.11.8 Sleep / Autonomy / Execution acceptance

- 端到端 synthetic acceptance。

## v0.11.9 Sleep / Reply / Dream conversation acceptance

- 用户聊天延迟入睡、call 打断、梦分享、第二天执行影响的验收。

## v0.11.10 Policy UX

- Sleep / Reply / Dream 策略层。
- presets：balanced、gentle、night_owl、workday、private、debug。

## v0.11.11 Policy conflicts / export / import / acceptance

- 策略冲突检查。
- 策略导入导出。
- 策略验收。

## v0.11.12 Human Review UX

- `/life review` 聚合睡眠、回复、梦、策略、doctor、proactive、FinalGate advisory。

## v0.11.13 Review action application

- review item preview/apply。
- action runs。

## v0.11.14 Review action policy / batch apply

- safe batch apply。
- 批量执行权限控制。

## v0.11.15 Review undo / rollback trace

- delayed reply release undo。
- recovery sleep plan undo。
- batch undo。

## v0.11.16 Agent-managed review loop

- heartbeat 后自动处理 safe review items，默认关闭。
- daily limit / failure budget / section allowlist。

## v0.11.17 Managed review acceptance / stress

- managed loop 验收。
- stress runner。
- tick_id 幂等。

## v0.11.18 Managed review observability / readiness

- observability report。
- release readiness report。
- `/life review` 显示 managed review 状态。
