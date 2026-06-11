# LifeEngine 总设计文档 v0.11.8 — Sleep / Autonomy / Execution 端到端验收

Plugin version: `0.11.8`  
Schema version: `28`  
sqlite-vec: required

## 目标

v0.11.8 不新增新的 Agent 生活大模块，而是把前面三条链路做成可重复运行的端到端验收层：

```text
SleepDayState / sleep debt
  ↓
Autonomy Planner
  ↓
Recovery sleep 或 downshifted goal step
  ↓
Narrative Execution Simulator
  ↓
partial / postponed / rescheduled
  ↓
Trace explain
```

这版验证的是：睡眠不足不只是被记录，还会影响自治规划和事件执行，并且结果可追踪。

## 新增数据表

```text
sleep_autonomy_execution_acceptance_runs
sleep_autonomy_execution_acceptance_scenarios
```

验收运行会使用一个 synthetic owner：

```text
<real_owner_id>-sae-<acceptance_run_id>
```

这样测试场景会留下可解释 trace，但不会污染真实 Agent owner 的生活。

## 验收场景

```text
SAE01_ALL_NIGHTER_AUTONOMY_RECOVERY
  通宵后的 SleepDayState 让 Autonomy 优先创建 recovery_sleep 计划。

SAE02_RECOVERY_EXISTS_GOAL_DOWNSHIFT
  已有补觉计划时，Autonomy 不重复创建补觉，而是把高强度目标降级为轻量推进。

SAE03_SHORT_SLEEP_IMPORTANT_EVENT_PARTIAL
  短睡后，重要工作事件执行为 partial，而不是硬完成。

SAE04_ALL_NIGHTER_LOW_IMPORTANCE_POSTPONED
  通宵/高睡眠压力下，低重要性工作事件会 postpone/reschedule。

SAE05_CALL_INTERRUPTS_SLEEP_AND_RELEASES
  life_call 能打断睡眠并释放 delayed replies。

SAE06_TRACE_COVERS_SLEEP_EXECUTION_CHAIN
  trace explain event 能看到 event transitions 与 execution_sleep_adjustments。
```

## 新增工具入口

通过 `life_upgrade`：

```json
{"action":"sleep_autonomy_execution_acceptance"}
{"action":"sleep_autonomy_execution_acceptance_runs"}
{"action":"sleep_autonomy_execution_acceptance_get", "acceptance_run_id":"..."}
```

别名：

```text
sae_acceptance
sleep_execution_acceptance
sae_acceptance_runs
sae_acceptance_get
```

## CLI / Slash

CLI：

```bash
hermes lifeengine upgrade sae_acceptance
hermes lifeengine upgrade sae_acceptance_runs
hermes lifeengine upgrade sae_acceptance_get <run_id>
```

Slash：

```text
/life upgrade sae_acceptance
/life upgrade sae_acceptance_runs
/life upgrade sae_acceptance_get <run_id>
```

## 闭环判断

v0.11.8 后，Sleep / Autonomy / Execution 不再只是分别有单元能力，而是具备验收级闭环：

```text
睡眠不足 → 自治补觉/降强度 → 到点执行 partial/postpone → trace 可解释
```

## v0.11.8 测试重点

```text
1. schema version 28 与 acceptance 表存在。
2. SAE acceptance 能跑 6 个真实 synthetic 场景。
3. 结果写入 acceptance runs/scenarios。
4. acceptance get/list 可查询。
5. synthetic owner 不等于真实 owner，避免污染真实 Agent 生活。
```
