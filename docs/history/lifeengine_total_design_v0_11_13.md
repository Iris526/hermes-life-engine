# LifeEngine 总设计文档 v0.11.13

## 版本定位

v0.11.13 是 **Human Review Action Application / 一键建议执行** 版本。

v0.11.12 已经把 Sleep / Reply / Dream / Policy / FinalGate / Proactive / Doctor / User Confirmation 等复杂维护面聚合到 `/life review`。v0.11.13 在此基础上让 review 不只是“看”，还可以安全地“做”。

核心原则不变：

```text
Review action 不能绕过 LifeEngine。
任何 durable life mutation 仍然必须走 LifeOps / Validator / Transaction / Journal / CommitReceipt / Trace。
不确定或高风险动作必须要求 explicit choice。
```

## Schema

```text
Plugin version: 0.11.13
Schema version: 33
sqlite-vec: required
```

新增表：

```text
human_review_action_runs
```

用于记录每一次 review item 的 preview / apply / failed / needs_choice / manual 状态。

字段包括：

```text
item_id
review_run_id
mode
status
input_json
plan_json
output_json
transaction_id
receipt_id
error
created_at
completed_at
```

## 人类命令面

普通用户仍然只需要少量命令：

```text
/life
/life setup <设定>
/life commit
/life pause
/life resume
/life run
/life call
/life dream
/life policy
/life review
/life doctor
/life backup
/life advanced
```

新增 review action：

```text
/life review preview <item_id>
/life review apply <item_id> [choice]
/life review actions
/life review get_action <action_run_id>
```

`choice` 用于消除歧义：

```text
confirm / reject
send / suppress
```

## Agent 工具面

新增/扩展：

```text
life_review(action="preview_action")
life_review(action="apply")
life_review(action="action_runs")
life_review(action="get_action")
```

Agent 可以读取 `/life review` 的 `action_hint`，再调用 `life_review` preview/apply。人类不需要记底层工具。

## 支持的 review item action

### sleep_state

```text
睡眠债 / 恢复压力需要注意
  → life_sleep recovery_plan
```

可创建 recovery sleep plan。

### delayed_reply

```text
有延迟回复待处理
  → RELEASE_DELAYED_REPLIES LifeOp
```

会释放 delayed replies，并生成 delayed reply digest。

### dream_audit_finding

```text
DreamAudit 发现待处理项
  → collect repair ops
  → LifeOps commit
```

通过 DreamAudit repair 机制提交修复，不直接改状态。

### user_confirmation

需要 explicit choice：

```text
/life review apply <item_id> confirm
/life review apply <item_id> reject
```

确认后将 proposed ops 转成 `source=user_confirmed` 并提交。

### proactive_intent

```text
Agent 有想说的话
  → EVALUATE_PROACTIVE_INTENT LifeOp
```

进入 delivery policy，不直接发消息。

### proactive_outbox

需要 explicit choice：

```text
send / suppress
```

避免 review 默认替用户发送主动消息。

### policy_conflict

如果有 suggested_patch，需要显式允许 policy patch；否则只返回 needs_choice/manual。

### final_gate / doctor

保持诊断性质，不自动修复。

## 闭环

Review action 的闭环：

```text
human_review_item
  ↓
plan_review_item_action
  ↓
preview / apply
  ↓
LifeOps or safe subsystem action
  ↓
Validator / transaction / journal / receipt when applicable
  ↓
human_review_action_runs
  ↓
human_review_items.status = applied / dismissed / open
```

## 不变量

```text
1. /life review 默认只展示，不自动改生活。
2. apply 必须显式触发。
3. ambiguous action 必须 explicit choice。
4. 用户侧 confirmation 不能自动确认。
5. proactive outbox 不能默认自动发送。
6. DreamAudit repair 必须经 LifeOps。
7. delayed replies release 必须经 LifeOps。
8. 所有 apply 结果必须写 human_review_action_runs。
```

## 测试覆盖

v0.11.13 新增测试覆盖：

```text
schema version 33
human_review_action_runs 表
review preview delayed_reply
review apply delayed_reply → release + receipt
user confirmation needs explicit choice
user confirmation confirm via review apply
/life review preview slash surface
```
