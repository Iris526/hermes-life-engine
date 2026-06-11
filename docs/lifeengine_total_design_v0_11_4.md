# LifeEngine 总设计文档 v0.11.4 — Sleep / Reply / Dream 端到端验收与 DreamAudit 修复应用

> v0.11.4 建立在 v0.11.0 Event V2、v0.11.1 SleepPlan/SleepSession、v0.11.2 ReplyGate、v0.11.3 DreamRun/DreamAudit/DreamEntry 之上。本版不新增大型生活能力，而是把睡眠、回复门禁、梦境自检三条链路做端到端收敛，并加入 DreamAudit finding 的显式修复应用路径。

```text
Plugin version: 0.11.4
Schema version: 24
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 为什么需要 v0.11.4

v0.11.3 已经能在睡眠结束后创建 DreamRun、运行 DreamAudit、生成 dream_symbolic DreamEntry，并产生可分享的 proactive intent。

但还缺两个发布前闭环：

```text
1. DreamAudit 发现问题以后，如何安全修复？
2. Sleep / Reply / Dream 是否有统一的端到端验收面？
```

v0.11.4 解决这两个问题。

## 2. DreamAudit 修复应用原则

DreamAudit 不是自动清账脚本。它只能产生 finding 和 proposed_ops。

修复必须走：

```text
DreamAuditFinding.proposed_ops
  ↓
LifeOps Validator
  ↓
LifeTransaction
  ↓
LifeJournal
  ↓
CommitReceipt
  ↓
DreamAuditFinding.status = resolved
```

这保证梦里的自检不会绕过 LifeEngine 的核心不变量。

## 3. 新增表

### 3.1 dream_repair_runs

记录一次 DreamAudit 修复预览或应用：

```text
id
owner_kind / owner_id
dream_run_id
mode: dry_run / noop / apply
status: planned / noop / applied / failed
finding_ids_json
proposed_ops_json
transaction_id
receipt_id
error
output_json
created_at / completed_at
```

### 3.2 sleep_reply_dream_acceptance_runs

记录一次 Sleep / Reply / Dream 验收运行：

```text
id
owner_kind / owner_id
status
scenario_count
passed_count
failed_count
summary_json
report_markdown
created_at / completed_at
```

### 3.3 sleep_reply_dream_acceptance_scenarios

记录具体验收场景：

```text
SRD01_SLEEP_DELAYED_BY_CHAT
SRD02_CALL_WAKES_SLEEP
SRD03_UNINTERRUPTIBLE_EVENT_DEFERS
SRD04_DREAM_AUDIT_REPAIR
SRD05_WAKE_SHARE_DREAM
SRD06_ALL_NIGHTER_STATE
```

## 4. 新增 Dream 操作

### repair_plan

只收集 open DreamAudit findings 的 proposed_ops，不应用。

```json
{"action":"repair_plan", "dream_run_id":"dreamrun_xxx"}
```

### repair / apply_repairs

把 proposed_ops 送入 LifeOps transaction。

```json
{"action":"repair", "dream_run_id":"dreamrun_xxx"}
```

如果成功：

```text
- 创建 LifeTransaction
- 创建 CommitReceipt
- 更新 dream_audit_findings.status = resolved
- 更新 resolved_by_tx_id
- 写 dream_repair_runs
```

### repairs / repair_runs

查看修复记录。

## 5. 新增 upgrade/acceptance 操作

```text
life_upgrade(action="sleep_reply_dream_acceptance")
life_upgrade(action="sleep_reply_dream_acceptance_runs")
life_upgrade(action="sleep_reply_dream_acceptance_get")
```

这个 acceptance 面记录的是 Sleep / Reply / Dream 的发布验收元数据。行为覆盖仍由 pytest 端到端测试保证。

## 6. 端到端闭环

### 6.1 睡眠与实际状态

```text
SleepPlan
  ↓
SleepSession
  ↓
RealtimeState.asleep / idle
  ↓
SleepSession actual duration / wake cause
```

### 6.2 回复门禁

```text
Incoming message
  ↓
ReplyGate decision
  ↓
allow / advisory / defer / call_override
  ↓
DelayedReply or CallOverride
```

### 6.3 DreamRun

```text
SleepSession wake
  ↓
DreamRun
  ↓
DreamAudit
  ↓
DreamEntry truth_layer=dream_symbolic
  ↓
Memory + ProactiveIntent
```

### 6.4 DreamAudit 修复

```text
DreamAuditFinding.open
  ↓
repair_plan
  ↓
LifeOps commit
  ↓
Finding resolved
```

## 7. 本版验收

v0.11.4 新增测试覆盖：

```text
1. schema version 24 和新增表存在
2. DreamAudit stale schedule block finding 可生成 repair_plan
3. repair 会把 proposed_ops 走 LifeOps 并更新 schedule status
4. finding 被标记 resolved_by_tx_id
5. sleep_reply_dream_acceptance 记录 6 个验收场景
```

完整回归结果：

```text
82 passed
```

## 8. 下一步

v0.11.4 已经把 Sleep / Reply / Dream 的基础闭环打通。下一步建议是 v0.11.5：

```text
- 真实 all-nighter 策略
- 睡眠不足对第二天 autonomy/resource 的影响
- DreamAudit repair policy: manual / auto_safe / off
- delayed replies 的更自然聚合回复生成
```
