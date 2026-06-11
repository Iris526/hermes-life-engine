# LifeEngine 总设计文档 v0.11.14

## 版本定位

v0.11.14 是 **Review Action Policy / 批量执行与权限控制** 版本。

v0.11.12 把 LifeEngine 的复杂状态聚合到 `/life review`；v0.11.13 让单个 review item 可以 preview/apply；v0.11.14 在此基础上补上策略层和批量层，让 Agent 或高级用户可以安全地处理一组 review 建议，而不是逐条操作。

```text
Plugin version: 0.11.14
Schema version: 34
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 设计目标

1. 人类命令面仍然简单，普通用户主要使用 `/life review`。
2. Agent 工具面保留完整能力，可以使用 `life_review` 自动处理安全建议。
3. 批量执行必须有策略、dry-run、allowlist/denylist 和 trace。
4. 用户侧确认、主动消息发送、策略 patch 等高风险动作不能被 safe batch 自动执行。
5. 所有真正改变状态的动作仍然必须通过既有 LifeOps / Validator / Transaction / Journal / Receipt。

## 新增表

### human_review_action_policies

每个 owner 一份 review action 策略。

关键字段：

```text
owner_kind
owner_id
policy_json
updated_by
updated_at
created_at
```

默认策略：

```json
{
  "mode": "manual",
  "allow_safe_batch": true,
  "allow_agent_safe_apply": true,
  "max_batch_items": 10,
  "default_safe_only": true,
  "safe_item_types": [
    "sleep_state",
    "delayed_reply",
    "dream_audit_finding",
    "proactive_intent",
    "policy_warning"
  ],
  "manual_choice_item_types": [
    "user_confirmation",
    "proactive_outbox",
    "policy_conflict"
  ],
  "deny_item_types": [
    "doctor_warning",
    "final_gate_feedback",
    "final_gate_report"
  ],
  "safe_sections": ["sleep", "reply", "dream", "proactive", "policy"],
  "require_dry_run_first": false,
  "allow_policy_patch": false
}
```

### human_review_batch_runs

记录一次批量 preview/apply。

```text
id
owner_kind / owner_id
review_run_id
mode: dry_run / apply
section
safe_only
status
selected_item_ids_json
plan_json
results_json
transaction_ids_json
receipt_ids_json
error
created_at / completed_at
```

### human_review_batch_items

记录批量 run 内每个 item 的处理结果。

```text
batch_run_id
item_id
action_run_id
status
plan_json
output_json
error
```

## 执行模型

### 单项执行

```text
review item
  ↓
preview_action
  ↓
plan_review_item_action
  ↓
apply_action
  ↓
LifeOps / direct safe path / confirmation path
  ↓
human_review_action_runs
```

### 批量执行

```text
/life review batch_preview [section]
  ↓
select open review items
  ↓
apply review action policy
  ↓
only safe_auto items selected
  ↓
record human_review_batch_runs(mode=dry_run)

/life review apply_all [section]
  ↓
select safe items
  ↓
for each item: _apply_review_action_locked
  ↓
record per-item human_review_action_runs
  ↓
record human_review_batch_runs + human_review_batch_items
```

## 安全规则

### 可以 safe batch 的 item

默认允许：

```text
sleep_state
  → recovery_sleep 建议

delayed_reply
  → release delayed replies / create digest

dream_audit_finding
  → DreamAudit safe repair ops

proactive_intent
  → evaluate proactive intent，不直接发送

policy_warning
  → 生成 policy suggestions，不直接 patch
```

### 需要显式 choice 的 item

```text
user_confirmation
  需要 confirm / reject

proactive_outbox
  需要 send / suppress

policy_conflict
  需要显式 allow_policy_patch 才能 patch
```

### 默认不会自动处理的诊断 item

```text
doctor_warning
final_gate_feedback
final_gate_report
```

这些只进入 review/trace，不自动改状态。

## 新增 life_review action

```text
policy / action_policy / get_policy
set_policy / patch_policy
validate_policy
batch_preview / preview_all / dry_run_all
apply_all / batch_apply / apply_safe / apply_section
batch_runs / batches
get_batch / batch_get
```

## 人类命令

```text
/life review
/life review preview <item_id>
/life review apply <item_id> [choice]
/life review policy
/life review batch_preview [section]
/life review apply_all [section]
/life review batch_runs
/life review get_batch <batch_run_id>
```

普通用户不需要记这些高级命令。`/life review` 会给出一页总览，Agent 可以根据 action_hint 自己调用工具。

## 闭环判断

v0.11.14 后，Human Review 闭环升级为：

```text
LifeEngine 状态
  ↓
/life review 聚合
  ↓
review item + action_hint
  ↓
review action policy
  ↓
preview / batch preview
  ↓
apply / batch apply
  ↓
LifeOps / validator / journal / receipt
  ↓
human_review_action_runs / human_review_batch_runs
  ↓
trace explain / doctor
```

## 测试覆盖

v0.11.14 新增测试覆盖：

```text
1. schema version 34 与 review action policy / batch tables 存在。
2. review action policy 可读取、patch、validate。
3. delayed_reply item 可以 batch_preview 并 batch_apply。
4. user_confirmation 不会被 safe batch 自动应用。
5. /life review batch_preview slash surface 可用。
```
