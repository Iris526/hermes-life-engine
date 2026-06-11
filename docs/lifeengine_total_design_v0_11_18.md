# LifeEngine 总设计文档 v0.11.18

v0.11.18 是 **Managed Review Observability / Release Readiness** 小版本。它不新增新的 Agent 生活领域模型，而是把 v0.11.16-v0.11.17 已经完成的 Agent Managed Review Loop 做成可观测、可诊断、可发布前验收的稳定层。

```text
Plugin version: 0.11.18
Schema version: 38
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 背景

v0.11.16 允许 Agent 在策略明确允许时，在 heartbeat 后自动处理 `/life review` 中的 safe items。
v0.11.17 增加了 managed review acceptance、stress runner 和 duplicate tick idempotency。

v0.11.18 的目标是让人类和 Agent 能一眼判断：

```text
Agent Managed Review Loop 现在安全吗？
它有没有开启？
今天跑了几次？
有没有失败？
验收和压力测试是否通过？
Doctor 有没有告警？
当前策略是否 release-ready？
```

## 2. 新增表

```text
human_review_managed_observability_reports
human_review_managed_release_readiness_reports
```

### human_review_managed_observability_reports

记录一次 Managed Review 观测快照：

```text
policy
policy_validation
managed_state
recent_runs
latest_acceptance
latest_stress
doctor
review_summary
signals
recommendations
rendered_text
```

### human_review_managed_release_readiness_reports

记录一次发布前 readiness 判断：

```text
checks
blockers
warnings
score
readiness_status
recommendation
observability_report_id
rendered_text
```

## 3. Observability 信号

`managed_observability` 聚合：

```text
Review Action Policy
Managed loop daily state
Recent managed loop runs
Latest managed acceptance run
Latest managed stress run
Doctor summary
Human review summary
```

输出状态：

```text
ready
needs_review
blocked
disabled
```

典型 signals：

```text
policy_conflicts
managed_loop_disabled
acceptance_not_passing
stress_not_passing
doctor_issues
last_managed_run_not_clean
managed_failures_today
```

## 4. Release readiness

`managed_release_readiness` 会生成一组检查：

```text
policy_has_no_conflicts
managed_loop_explicitly_enabled
daily_limit_positive
acceptance_passed
stress_passed
doctor_clean
failure_budget_available
```

其中 required checks 失败会进入 blocker。
optional checks 失败会进入 warning。

readiness 状态：

```text
ready
ready_with_warnings
blocked
```

## 5. Review 页面集成

`/life review` 现在会在 summary 中加入：

```text
Managed Review：enabled=True today runs=... actions=... failures=... acceptance=... stress=...
```

如果 managed loop 已开启但验收/压力测试未通过，review 会增加 warning item：

```text
Managed Review 尚未通过验收
Managed Review 尚未通过压力测试
Managed Review 今日有失败记录
```

这些 item 只是提醒，不会自动改变生活状态。

## 6. 新增 tool / CLI / Slash

`life_review` 新增 action：

```text
managed_observability
managed_observability_reports
get_managed_observability
managed_release_readiness
managed_release_readiness_reports
get_managed_release_readiness
```

Slash：

```text
/life review managed_observability
/life review managed_observability_reports
/life review get_managed_observability <report_id>
/life review managed_readiness
/life review managed_readiness_reports
/life review get_managed_readiness <report_id>
```

CLI：

```bash
hermes lifeengine review managed_observability
hermes lifeengine review managed_observability_reports
hermes lifeengine review get_managed_observability <report_id>
hermes lifeengine review managed_readiness
hermes lifeengine review managed_readiness_reports
hermes lifeengine review get_managed_readiness <report_id>
```

## 7. 安全边界

v0.11.18 不会自动开启 Managed Review。

```text
allow_agent_managed_loop = false
```

仍然是默认安全策略。

Observability / readiness 报告只做诊断和建议，不会：

```text
确认用户事实
发送主动消息
修改策略
执行 batch apply
改变 Agent 生活事件
```

## 8. 当前闭环

截至 v0.11.18：

```text
Event V2: 已实现
Realtime State: 已实现
SleepPlan / SleepSession: 已实现
ReplyGate / delayed replies / life_call: 已实现
DreamRun / DreamAudit / DreamEntry: 已实现
Sleep-aware Autonomy / Execution: 已实现
Human Review UX: 已实现
Review Action Application: 已实现
Review Action Policy / safe batch apply: 已实现
Review Undo / rollback trace: 已实现
Agent Managed Review Loop: 已实现
Managed Review Acceptance / Stress: 已实现
Managed Review Observability / Release Readiness: 已实现
```

下一步建议：**v0.11.19 Managed Review UX polish & docs freeze**，把 release readiness 接入 `/life doctor`、README 和 v1.0-rc checklist，并整理最终人类命令手册。
