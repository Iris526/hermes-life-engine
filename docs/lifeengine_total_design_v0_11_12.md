# LifeEngine 总设计文档 v0.11.12

## 版本定位

v0.11.12 是 Human Review UX 收敛版本。它不增加新的 Agent 生活行为模块，而是把前面已经完成的 Sleep / Reply / Dream / Policy / FinalGate / Proactive / Doctor / User Confirmation 等维护面聚合成一个人类可读的一页式 review。

## 核心目标

此前 LifeEngine 的内部工具面已经很完整，但人类命令过多，普通用户不应该记住几十个高级命令。v0.11.12 的目标是：

```text
人类只看 /life review
Agent 继续使用完整 life_* tools
高级调试保留 /life advanced
```

## 新增概念：Human Review Run

Human Review Run 是一次聚合视图，不是生活事实，不推进时间，不创建生活事件。

它会汇总：

```text
1. engine / realtime state
2. SleepDayState：睡眠债、恢复压力、是否通宵、是否建议补觉
3. pending user confirmations
4. delayed replies / delayed reply digests
5. DreamAudit findings / recent dream entries
6. proactive intents / proactive outbox
7. FinalGate advisory / internal feedback queue
8. Sleep / Reply / Dream policy conflicts and warnings
9. Doctor warnings
```

## 新增数据表

```text
human_review_runs
human_review_items
```

### human_review_runs

记录一次 review 生成结果：

```text
id
owner_kind / owner_id
status
severity
summary_json
section_counts_json
item_count
rendered_text
created_at
```

### human_review_items

记录 review 中的每个待办或提醒项：

```text
id
owner_kind / owner_id
review_run_id
item_type
severity
title
message
source_table
source_id
action_hint_json
status
created_at
resolved_at
```

## 新增工具

```text
life_review
```

支持：

```text
summary / run / review / page / status
runs / history / list
get_run / explain
dismiss / resolve
```

## 人类命令面

普通用户仍然只需要：

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

`/life review` 会输出类似：

```text
LifeEngine Review
=================
状态：active / realtime=idle
睡眠：睡眠债 120 分钟，恢复压力 70，通宵=False，建议补觉=True
策略：balanced，冲突 0，提醒 1
Doctor：有提醒，issues=1

待处理 / 建议：
1. [warning] 睡眠债 / 恢复压力需要注意 — 睡眠债 120 分钟，恢复压力 70。
   建议工具：life_sleep action=recovery_plan
2. [action] 有延迟回复待处理 — 用户睡前发来的消息……
   建议工具：life_reply action=release
```

## 闭环原则

Human Review 不改变 LifeEngine 的核心不变量：

```text
1. Review 不创建生活事实。
2. Review 不推进 heartbeat。
3. Review 不执行 DreamAudit 修复。
4. Review 只聚合并提示。
5. 真正修复仍然通过对应 life_* tool 和 LifeOps。
```

## 与 Agent 工具面的关系

人类看到一页 review；Agent 可以根据 review 中的 action_hint 调用：

```text
life_sleep recovery_plan
life_reply release
life_dream repair_plan / repair
life_policy conflicts / suggestions
life_confirmation confirm / reject
life_proactive evaluate / send / suppress
life_final_gate get
life_doctor
```

这满足产品原则：

```text
人类命令面简单。
Agent 工具面完整。
Trace / audit 仍然完整。
```

## 当前状态

截至 v0.11.12：

```text
Event V2: 已实现
Realtime State: 已实现
SleepPlan / SleepSession: 已实现
ReplyGate / delayed replies / life_call: 已实现
DreamRun / DreamAudit / DreamEntry: 已实现
Sleep-aware Autonomy / Execution: 已实现
Sleep / Reply / Dream Policy UX: 已实现
Policy conflicts / export / import / acceptance: 已实现
Human Review UX aggregation: 已实现
```

## 下一步建议

v0.11.13 建议做：

```text
Review Action Application / 一键建议执行
```

也就是让 review item 的 action_hint 可以被 Agent 或高级用户选择性 apply，但仍然通过 LifeOps / validator / trace，而不是直接改状态。
