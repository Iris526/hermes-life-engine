# LifeEngine 总设计文档 v0.10.0

> **v0.10.0 是把 v0.99 验证线与真实使用反馈合并后的完整候选包。**
>
> 它不新增新的 Agent 生活行为模块，而是把 v0.9.7 / v0.99 已完成的验收、发布、trace 覆盖、FinalGate 语义修复，与最近真实使用中发现的人类命令面和 FinalGate 用户体验问题合并为一个完整包。

## 0. 版本信息

```text
Plugin version: 0.10.0
Schema version: 19
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
Storage: SQLite + FTS5 + sqlite-vec
```

v0.10.0 的目标不是继续堆生活能力，而是给 v1.0-rc 前提供一个完整、可安装、可测试、可追踪、可解释、体验更合理的候选包。

## 1. 继承自 v0.99 的能力

v0.99 已经是 v1.0-rc 前测试验证候选版，包含：

```text
1. FinalGate 语义匹配漏洞修复
2. 失败 LifeOps durable trace
3. trace explain <event_id> 完整上下文
4. trace coverage doctor
5. 65 passed 测试矩阵
6. Acceptance suite 5/5 scenarios passed
7. Schema version 18
```

v0.10.0 在这个基础上升级到 schema 19，并合并真实使用反馈。

## 2. 本轮真实使用反馈

真实聊天截图暴露了两个产品级问题：

### 2.1 人类命令面过大

LifeEngine 有大量能力：resource、inventory、truth、goal、autonomy、proactive、execution、confirmation、upgrade、trace 等。

这些能力应该开放给 Agent 和高级调试，但不应该要求普通用户记住。

v0.10.0 明确分层：

```text
Human surface: small
Agent tool surface: complete
Advanced/debug surface: available but hidden behind /life advanced
```

普通用户只需要记住：

```text
/life
/life help
/life setup <设定>
/life commit
/life pause
/life resume
/life run
/life review
/life doctor
/life backup
/life advanced
```

### 2.2 FinalGate 不应把内部审计暴露给用户

旧行为中，FinalGate 可能把内部诊断文本直接发给用户：

```text
LifeEngine 拦截了这次最终回复……
缺少 CommitReceipt 或 canonical state 证据……
建议 LifeOps 草案……
```

这是错误的产品体验。FinalGate 的主要作用应该是提醒 Agent 和写 trace，而不是把内部门禁错误原样给用户。

v0.10.0 默认：

```text
final_audit = advisory
```

即：

```text
发现 unsupported hard claim
  ↓
写 final_gate_reports
  ↓
写 final_gate_feedback_queue
  ↓
下一轮 pre_llm_call 内部提示 Agent
  ↓
用户可见回复默认放行
```

## 3. FinalGate v0.10.0 语义

### 3.1 claim 分类

FinalGate 将最终回答里的生活 claim 分为：

```text
hard claim:
  已完成事实、过去经历、资源变化、拥有物品、钱包余额、已执行结果。

soft claim:
  意图、计划草案、安排大概是、今天准备、我想、我可能、我打算。
```

soft claim 默认不阻断，只进入 advisory trace。

例如：

```text
“今天安排大概是：上午收拾符纸，10 点半过去。”
```

这是 plan / intent / draft schedule，不应被当成缺少 receipt 的已完成事实硬拦截。

### 3.2 语义匹配硬化

v0.99 修复了中文匹配过宽问题：

```text
已提交：今天中午吃了咖喱饭
不能支持：我今天中午买了一条裙子
不能支持：我今天中午去了巴黎
```

规则：

```text
1. 完整子串匹配直接通过。
2. 普通 token overlap 不足够。
3. 时间词、代词、通用状态词不能单独证明事实。
4. 有具体动作的 claim 必须有兼容动作 evidence。
5. 有对象域的 claim 应尽量匹配对象域。
```

### 3.3 模式

```text
advisory:
  默认。记录报告和内部反馈，不替换用户回复。

strict:
  高级调试/安全模式。可替换回复，但有 intervention budget。

repair:
  返回软修正文案。

trace:
  只记录，不影响回复。

warn:
  可追加轻量 warning。
```

### 3.4 intervention budget

同一个 `session_id + turn_id` 最多干预 3 次。

超过后：

```text
released_after_budget
```

即放行，同时写 trace，不会无限拦截。

## 4. v0.10.0 新增/确认 schema

Schema version: `19`

继承关键 schema：

```text
final_gate_reports
final_gate_feedback_queue
trace_coverage_reports
failed_lifeops_audits
acceptance_scenario_runs
acceptance_reports
v1_rc_checklists
integration_test_runs
api_freeze_snapshots
release_readiness_reports
command_surface_profiles
v010_release_notes
```

## 5. 失败 LifeOps durable trace

失败的 LifeOps 不能污染生活事实，但必须可审计。

```text
commit_ops 失败
  ↓
主 transaction rollback
  ↓
单独写 trace_runs(trace_type='life_commit_failed')
  ↓
写 audit_log(life_commit_failed)
  ↓
写 failed_lifeops_audits
  ↓
不创建 life_transactions / life_ops / life_journal 生活事实
```

## 6. Trace coverage doctor

Trace coverage doctor 检查每个 committed transaction 是否具备：

```text
trace_id
trace_runs row
life_ops rows
life_ops.status = committed
validator_report_json
per-op life_journal
commit_receipt
commit_receipt_facts
```

这样 LifeEngine 不只是“有 trace”，而是可以验证 trace 是否覆盖完整闭环。

## 7. Trace explain event 完整上下文

`/life trace explain <event_id>` 返回：

```text
event
actions
results
resource_ledger
schedule_blocks
wake_jobs
memories
goal_links
dependencies
execution_decisions
serendipity
proactive_intents
diary_entries
journal
```

它应该能回答：

```text
这件事为什么存在？
什么时候计划的？
哪个 heartbeat 执行了？
执行结果是什么？
有什么资源变化？
有没有 diary / proactive / serendipity？
最终回答依据哪个 receipt？
```

## 8. v0.10.0 command surface

### 8.1 Human surface

```text
/life
/life help
/life setup <设定>
/life commit
/life pause
/life resume
/life run
/life review
/life doctor
/life backup
/life advanced
```

### 8.2 Agent tool surface

```text
life_status
life_upgrade
life_doctor
life_control
life_setup
life_commit
life_resource
life_event
life_memory
life_tick
life_diary
life_trace
life_final_gate
life_truth
life_inventory
life_confirmation
life_goal
life_autonomy
life_proactive
life_execution
```

Agent 应通过这些 tools 自己完成 LifeOps、资源、日程、trace、truth source、确认流和维护动作。

### 8.3 Advanced surface

`/life advanced` 显示完整高级能力，包括：

```text
truth
resource
inventory
goal
autonomy
proactive
execution
confirmation
trace
final_gate
upgrade
heartbeat
module
```

## 9. Acceptance / release readiness

v0.10.0 继承 v0.9.7/v0.99 验收层：

```text
acceptance
acceptance_reports
acceptance_report
acceptance_runs
v1_rc_checklists
integration_check
surface
api_freeze
api_freeze_status
release_readiness
mandatory_gate_patch
```

内置五个 v1.0-rc 验收场景：

```text
S01_SETUP_CANON_PAUSE_GATING
S02_AGENT_GOAL_HEARTBEAT_EXECUTION
S03_TRUTH_WEATHER_POSTPONE
S04_USER_CONFIRMATION_POLICY
S05_RELEASE_READINESS_TRACE
```

## 10. 当前闭环状态

截至 v0.10.0：

```text
Canon 设定闭环：已实现
Pause/setup 防污染闭环：已实现
LifeOps mutation 闭环：已实现
CommitReceipt / FinalGate evidence 闭环：已实现
FinalGate advisory UX：已实现
FinalGate semantic matching hardening：已实现
Failed LifeOps durable trace：已实现
Scalar Resource Ledger 闭环：已实现
Entity Inventory / Meals 闭环：已实现
TruthSource resolve / observe / cache / trace 闭环：已实现
WakeJob Heartbeat 闭环：已实现
User Life Confirmation 闭环：已实现
Goals / Life Arcs / Event Decomposition 闭环：已实现
Reflection 复盘闭环：已实现
Autonomy Planner 闭环：已实现
Proactive Intent / Outbox / State 闭环：已实现
Narrative Execution / Serendipity 闭环：已实现
Trace hash-chain verify：已实现
Trace coverage doctor：已实现
Lifecycle validator：已实现
Doctor / invariant checks：已实现
Install / migration checks：已实现
Heartbeat cron diagnostics：已实现
Export / import / staged restore：已实现
Package manifest / checksum：已实现
Index rebuild / verification：已实现
Concurrency smoke：已实现
Hermes integration acceptance：已实现
API freeze snapshot：已实现
v1.0-rc scenario acceptance report：已实现
```

## 11. 测试状态

本包新增 v0.10.0 回归测试，覆盖：

```text
1. version/schema/table surface
2. FinalGate 中文语义匹配不再靠时间词 overlap
3. failed LifeOps durable trace
4. trace explain event full context
5. trace coverage doctor
6. acceptance/release/surface/API freeze helpers
7. simple human command surface
```

本轮测试结果：

```text
56 passed
```

## 12. v1.0-rc 前建议

v0.10.0 后可以进入 v1.0-rc 准备：

```text
1. 冻结 DB schema。
2. 冻结 LifeOps 类型。
3. 冻结 Hermes tool schemas。
4. 冻结 CLI/slash command surface。
5. 只接受阻断性 bugfix、安装修复、文档修复、兼容性修复。
6. 在真实 Hermes profile 中做长时间 heartbeat/gateway/multi-session soak test。
```
