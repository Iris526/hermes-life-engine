# LifeEngine 总设计文档 v0.11.17

v0.11.17 是 **Agent Managed Review Loop Acceptance & Stress** 小版本。它不新增新的生活领域模型，而是把 v0.11.16 的 Agent Managed Review Loop 做成可验收、可压测、可幂等解释的稳定层。

```text
Plugin version: 0.11.17
Schema version: 37
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 目标

v0.11.16 已经允许 Agent 在策略允许时，在 heartbeat 后自动处理 `/life review` 中的 safe items。v0.11.17 的目标是确认这套自动循环不会失控：

```text
1. 默认不开启，不能自动改状态。
2. 显式开启后，只能处理 safe items。
3. daily action limit 必须生效。
4. failure budget 必须生效。
5. heartbeat 重复 tick 不能重复处理同一批事项。
6. 大量 delayed replies 下必须尊重 batch limit。
7. 验收和压力测试必须使用 synthetic owner，不污染真实 Agent life。
```

## 新增表

```text
human_review_managed_acceptance_runs
human_review_managed_acceptance_scenarios
human_review_managed_stress_runs
```

这些表只记录验收、压测和稳定性信息，不创建生活事实，不推进 Agent 生活，不影响真实 owner 的日常状态。

## Acceptance Runner

新增 `life_review(action="managed_acceptance")`。

它会创建 synthetic owner：

```text
<owner_id>-mgrev-<run_id>
```

然后执行 5 个验收场景：

```text
MGR01_DISABLED_BY_DEFAULT
  默认策略下 managed review loop 被阻止，delayed reply 保持 pending。

MGR02_SAFE_ITEM_APPLIED
  显式允许后，safe delayed_reply item 可以自动 release。

MGR03_DAILY_LIMIT
  daily action limit 达到后，后续 managed review run 被 blocked/skipped。

MGR04_DUPLICATE_TICK_IDEMPOTENCY
  同一个 heartbeat tick_id 重复进入时，不会重复执行。

MGR05_STRESS_LIMITED_BATCH
  批量 delayed replies 压测下，apply 数量不超过 policy limit。
```

## Stress Runner

新增 `life_review(action="managed_stress")`。

它会在 synthetic owner 下创建大量 delayed replies，然后开启 managed review loop，并检查：

```text
created_count
selected_count
applied_count
failed_count
released_count
pending_count
duration_ms
```

核心不变量：

```text
applied_count <= limit
released_count == applied_count
真实 owner 不被污染
```

## Duplicate tick idempotency

v0.11.17 在 `_run_agent_managed_review_locked` 中加入 tick_id 幂等保护：

```text
如果同一个 owner + tick_id 已有 managed loop run，直接返回 duplicate_tick，不再 apply。
```

这防止：

```text
gateway 多进程重复 tick
cron 重试
heartbeat retry
同一 tick 被手动和自动重复触发
```

造成重复释放 delayed replies 或重复创建 review action run。

## 新增 tool / slash / CLI surface

Tool：

```text
life_review(action="managed_acceptance")
life_review(action="managed_acceptance_runs")
life_review(action="get_managed_acceptance")
life_review(action="managed_stress")
life_review(action="managed_stress_runs")
life_review(action="get_managed_stress")
```

Slash：

```text
/life review managed_acceptance
/life review managed_acceptance_runs
/life review get_managed_acceptance <run_id>
/life review managed_stress [count]
/life review managed_stress_runs
/life review get_managed_stress <run_id>
```

CLI：

```bash
hermes lifeengine review managed_acceptance
hermes lifeengine review managed_acceptance_runs
hermes lifeengine review get_managed_acceptance <run_id>
hermes lifeengine review managed_stress --count 25 --limit 10
hermes lifeengine review managed_stress_runs
hermes lifeengine review get_managed_stress <run_id>
```

## 不变量

```text
1. managed review 默认关闭。
2. managed review 只能处理 policy safe-auto items。
3. user_confirmation 不得被 managed review 自动 confirm/reject。
4. proactive_outbox 不得被 managed review 自动 send/suppress。
5. policy patch 不得被 managed review 自动应用。
6. batch apply 仍然写 human_review_batch_runs / human_review_action_runs。
7. 每次 managed review 仍然写 human_review_managed_loop_runs。
8. acceptance/stress 只用 synthetic owner。
9. 同 tick_id 幂等。
10. 所有结果可以通过 trace / review runs 查询。
```

## 当前状态

截至 v0.11.17：

```text
Event V2: 已实现
Realtime State: 已实现
SleepPlan / SleepSession: 已实现
ReplyGate / delayed replies / life_call: 已实现
DreamRun / DreamAudit / DreamEntry: 已实现
DreamAudit repair application: 已实现
Sleep debt / all-nighter / recovery plan: 已实现
Sleep-aware Autonomy: 已实现
Sleep-aware Execution: 已实现
Human Review UX aggregation: 已实现
Review action application / batch apply / undo trace: 已实现
Agent Managed Review Loop: 已实现
Agent Managed Review Loop Acceptance & Stress: 已实现
```

下一步建议：v0.11.18 做 **Managed Review observability polish / release readiness**，把 acceptance、stress、doctor、review policy、managed loop state 聚合到 `/life review` 和 `/life doctor` 的发布前诊断视图里。
