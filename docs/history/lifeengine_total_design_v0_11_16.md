# LifeEngine 总设计文档 v0.11.16

v0.11.16 是 **Agent Managed Review Loop** 小版本。它不新增新的生活领域模型，而是在 v0.11.12-v0.11.15 的 Human Review / Review Action / Batch Apply / Undo Trace 基础上，让 Agent 在策略明确允许时，可以在 heartbeat 之后自动预览并应用安全 review 项。

```text
Plugin version: 0.11.16
Schema version: 36
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 设计动机

之前 `/life review` 已经能聚合睡眠债、延迟回复、DreamAudit finding、proactive intent、策略冲突、FinalGate advisory 和 doctor warning；v0.11.13-v0.11.14 又支持单项 apply 与安全批量 apply；v0.11.15 支持部分安全动作 undo。

但如果所有安全维护项都必须用户手动处理，Agent 的生活维护仍然太被动。v0.11.16 的目标是：

```text
Agent 可以自己处理低风险维护项，
但必须受策略、daily limit、failure budget、section allowlist、trace 和 undo 约束。
```

## 2. 新增数据表

### human_review_managed_loop_state

按 owner + date_key 记录当天 Agent 自动 review 的预算状态：

```text
run_count
action_count
failure_count
last_run_id
last_run_at
last_status
```

### human_review_managed_loop_runs

记录每次 Agent Managed Review Loop：

```text
trigger_source: heartbeat / manual / cli / slash
tick_id
status: planned / applied / noop / skipped / blocked / partial / failed
policy_json
decision_json
review_run_id
batch_run_id
selected_count
applied_count
skipped_count
failed_count
daily_action_count_before
daily_action_limit
failure_count_before
failure_budget
output_json
error
```

## 3. Review Action Policy 新增字段

v0.11.16 扩展 `human_review_action_policies.policy_json`：

```json
{
  "allow_agent_managed_loop": false,
  "agent_managed_sections": ["sleep", "reply", "dream", "proactive", "policy"],
  "agent_managed_daily_action_limit": 5,
  "agent_managed_failure_budget": 2,
  "agent_managed_min_minutes_between_runs": 20,
  "agent_managed_safe_only": true,
  "agent_managed_trigger_sources": ["heartbeat"]
}
```

默认 `allow_agent_managed_loop=false`，也就是说不会突然开始自动处理 review 项。需要明确开启。

## 4. 执行流程

```text
Heartbeat tick
  ↓
Sleep / Dream / Execution / Autonomy / Proactive
  ↓
Agent Managed Review decision
  ↓
读取 Review Action Policy
  ↓
检查 daily limit / failure budget / trigger source / policy conflicts
  ↓
生成 /life review 页面
  ↓
select safe item by section allowlist
  ↓
apply_all safe selected items
  ↓
记录 human_review_batch_runs
  ↓
记录 human_review_managed_loop_runs
  ↓
更新 human_review_managed_loop_state
```

## 5. 安全边界

Agent Managed Review Loop 只处理 Review Action Policy 允许的 safe items：

```text
sleep_state
  创建 recovery_sleep 补觉计划。

delayed_reply
  release delayed replies，并生成 digest。

dream_audit_finding
  只应用 DreamAudit safe repair LifeOps。

proactive_intent
  evaluate intent，不直接 send。

policy_warning
  记录 policy suggestions，不直接 patch。
```

默认不会自动处理：

```text
user_confirmation
proactive_outbox
policy_conflict
doctor_warning
final_gate_feedback
final_gate_report
```

这些仍然需要人工或 Agent 显式工具调用，并遵守原有确认/发送/策略 patch 规则。

## 6. 新增 tool / slash / CLI

`life_review` 新增 action：

```text
managed_preview / agent_preview / agent_managed_preview
managed_run / agent_run / agent_managed_run
managed_state / agent_state / managed_loop_state
managed_runs / agent_runs / managed_loop_runs
get_managed_run / managed_get / agent_run_get
```

Slash：

```text
/life review managed_preview
/life review managed_run
/life review managed_state
/life review managed_runs
/life review get_managed_run <run_id>
```

CLI：

```bash
hermes lifeengine review managed_preview
hermes lifeengine review managed_run
hermes lifeengine review managed_state
hermes lifeengine review managed_runs
hermes lifeengine review get_managed_run <run_id>
```

## 7. 为什么不是完全自动管家

v0.11.16 不是让 Agent 无限制改自己的状态。它只是允许 Agent 在 heartbeat 后处理明确低风险、可追踪、可 undo 的 review 维护项。关键不变量：

```text
1. 默认不开启 agent managed loop。
2. 只处理 safe_auto item。
3. 每天有 action limit。
4. 有 failure budget。
5. 所有 apply 仍走 LifeOps / batch run / action run。
6. 所有 managed run 都写 trace/audit/state。
7. 用户确认、主动发送、策略 patch 不默认自动执行。
```

## 8. 当前闭环状态

截至 v0.11.16：

```text
Human Review UX aggregation: 已实现
Review action application: 已实现
Review action policy / safe batch apply: 已实现
Review undo / rollback trace: 已实现
Agent Managed Review Loop: 已实现
```

下一步建议：**v0.11.17 Review Managed Loop Acceptance & Stress**，验证 heartbeat 自动 review 在多 session、重复 tick、失败预算、daily limit、undo trace 下的端到端稳定性。
