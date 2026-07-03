# LifeEngine 总设计文档 v0.11.3 — DreamRun / DreamAudit / DreamEntry

> v0.11.3 建立在 Event V2、SleepPlan/SleepSession 与 ReplyGate 之上，补上睡眠期间的 Dream 系统。Dream 不是真实世界事实，而是 `dream_symbolic` 层的自我整理、状态核查、记忆压缩与醒来后分享意图。

```text
Plugin version: 0.11.3
Schema version: 23
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 设计目标

Dream 系统解决三个问题：

```text
1. 睡眠时自检 LifeEngine 状态。
   检查有没有漏掉的状态流转、资源结算、卡住的 wake job、未释放的 delayed reply。

2. 睡眠时整理近期记忆。
   把近期事件、情绪、目标和状态压缩成一个梦，而不是把普通流水直接展示给用户。

3. 醒来后产生可分享内容。
   Dream 可以生成 proactive intent，但默认仍由 Proactive policy 控制，不直接强制推送。
```

核心原则：

```text
Dream is symbolic, not factual.
DreamAudit can detect issues, but fixes still go through LifeOps.
DreamEntry can become memory, but not canonical external truth.
Dream sharing is proactive intent, not automatic user spam.
```

## 2. Dream 的真相层

DreamEntry 固定使用：

```text
truth_layer = dream_symbolic
```

含义：

```text
梦可以表达 Agent 的内在状态、近期记忆与象征性联想。
梦不能证明现实中发生过某件事。
梦不能直接增加钱包、物品、日程、用户事实。
梦可以产生 reflection memory 或 proactive intent。
```

例如：

```text
梦见自己在雨棚下核对清单。
```

这不代表现实中去了雨棚。它可以表示：

```text
近期工作压力
DreamAudit 在核查漏结算
一个未完成目标的心理投影
```

## 3. 新增数据表

### 3.1 dream_runs

一次 DreamRun 对应一次睡眠后的梦流程。

```text
dream_runs
  id
  owner_kind / owner_id
  sleep_session_id
  dream_entry_id
  proactive_intent_id
  run_type: core_sleep / nap / manual
  status: running / completed / skipped / failed
  started_at / completed_at
  audit_status
  share_status
  trace_id
  canon_version
  metadata_json
```

### 3.2 dream_audit_findings

DreamAudit 的发现项。

```text
dream_audit_findings
  id
  dream_run_id
  owner_kind / owner_id
  finding_type
  severity: info / warn / error
  status: open / acknowledged / resolved / ignored
  summary
  proposed_ops_json
  evidence_json
  created_at
  resolved_at
  transaction_id
```

Finding 类型包括：

```text
resource_reconcile
stale_schedule_blocks
stuck_wake_jobs
pending_delayed_replies
stale_resource_reservations
dream_skipped
```

### 3.3 dream_entries

梦本身的叙事记录。

```text
dream_entries
  id
  owner_kind / owner_id
  dream_run_id
  sleep_session_id
  title
  summary
  content
  truth_layer = dream_symbolic
  symbols_json
  source_memory_ids_json
  source_event_ids_json
  source_goal_ids_json
  emotional_tone
  share_text
  created_memory_id
  created_at
```

## 4. 新增 LifeOps

### 4.1 RUN_DREAM

运行完整 Dream cycle：

```text
RUN_DREAM
  ↓
create dream_run
  ↓
DreamAudit
  ↓
DreamEntry generation
  ↓
Memory create
  ↓
optional ProactiveIntent
  ↓
CommitReceipt
```

Payload 示例：

```json
{
  "sleep_session_id": "sleep_session_xxx",
  "run_type": "core_sleep",
  "force": false,
  "allow_nap": false,
  "create_share_intent": true,
  "target_user_id": "default-user"
}
```

### 4.2 CREATE_DREAM_ENTRY

只创建一个 dream_symbolic 梦条目。

```json
{
  "content": "我梦见自己在雨棚下核对一张清单。",
  "summary": "雨棚下的清单",
  "truth_layer": "dream_symbolic"
}
```

该 op 会强制把 truth_layer 保持为 `dream_symbolic`。

## 5. Heartbeat 集成

v0.11.3 的 sleep wake 流程：

```text
sleep_plan_wake wake job
  ↓
WAKE_SLEEP_SESSION
  ↓
SleepSession completed / interrupted
  ↓
RealtimeState idle
  ↓
if dream gate permits:
    RUN_DREAM
      ↓
      DreamAudit
      DreamEntry
      Memory
      ProactiveIntent
```

默认规则：

```text
core_sleep 且实际睡眠时长 >= min_core_dream_minutes:
  可以运行 Dream。

nap:
  默认跳过 Dream，除非 Canon dream.allow_nap_dreams = true。

force=true:
  手动运行 Dream，不受最小时长限制。
```

## 6. DreamAudit 查什么

DreamAudit 不只是写梦，它先检查 LifeEngine 状态：

```text
1. resource_reconcile
   resource_accounts 是否和 resource_ledger 一致。

2. stale_schedule_blocks
   过去 24 小时里有没有 planned/in_progress 但没有结算的 schedule block。

3. stuck_wake_jobs
   有没有 running 太久的 wake job。

4. pending_delayed_replies
   ReplyGate 是否有未释放 delayed replies。

5. stale_resource_reservations
   是否有过期 resource reservations。
```

DreamAudit 的发现不会直接乱修复。它们会被写入 `dream_audit_findings`，必要时包含 `proposed_ops_json`，后续仍由 LifeOps validator 和 transaction 处理。

## 7. DreamEntry 如何生成

当前 v0.11.3 是 deterministic symbolic summary，不调用 LLM：

```text
读取近期 memories
读取近期 events
读取 active goals
读取 DreamAudit findings
生成一个象征性 dream content
创建 dream memory
可创建 proactive intent
```

后续可以接入 LLM narrative tick，但必须保持：

```text
DreamEntry.truth_layer = dream_symbolic
Dream claims do not enter canonical reality
```

## 8. 醒来分享

如果 `create_share_intent=true`，DreamRun 会创建：

```text
ProactiveIntent:
  intent_type = self_reflection_share
  summary = 梦的简述
  trigger = dream_entry_id
```

它不会直接发送给用户。发送仍由 Proactive mode 决定：

```text
pending_only:
  下次用户来时可以自然提起。

auto_send:
  进入 outbox queued，但仍受 daily limit / privacy / quiet hours 约束。
```

## 9. 新增工具与命令

### Tool

```text
life_dream
```

支持：

```text
status / state
run / cycle / dream
audit
list / runs
entries / dreams
get / get_run
get_entry
findings / audit_findings
create_entry
```

### Slash

```text
/life dream status
/life dream run
/life dream entries
/life dream findings
/life dream get <dream_run_id>
```

### CLI

```bash
hermes lifeengine dream status
hermes lifeengine dream run
hermes lifeengine dream entries
hermes lifeengine dream findings
hermes lifeengine dream get --dream-run-id <id>
```

## 10. Doctor / Trace

Doctor 新增：

```text
dreams:
  completed/interrupted core sleep >= 90min 但没有 dream_run → warn
  running dream_run 超过 30 分钟 → warn
```

Trace explain 可以追踪：

```text
SleepSession
  ↓
WakeJob
  ↓
RUN_DREAM transaction
  ↓
DreamRun
  ↓
DreamAuditFindings
  ↓
DreamEntry
  ↓
Memory
  ↓
ProactiveIntent
```

## 11. 与 ReplyGate / Sleep 的关系

```text
SleepSession 负责实际睡眠。
ReplyGate 负责睡眠中用户消息是否延迟、叫醒、强制 call。
DreamRun 负责睡眠后自检和梦境整理。
```

这三者互相独立，但通过 realtime state 和 trace 连接。

## 12. 当前实现边界

v0.11.3 已实现：

```text
DreamRun / DreamAudit / DreamEntry 数据层
RUN_DREAM / CREATE_DREAM_ENTRY LifeOps
Heartbeat wake 后自动 Dream
Dream memory
Dream proactive share intent
Doctor dream checks
life_dream tool / slash / CLI
```

尚未实现：

```text
LLM-based dream narrative tick
DreamAudit 自动修复 proposed ops
复杂梦境符号学习
跨多夜梦境主题追踪
```

## 13. 测试

v0.11.3 包含测试覆盖：

```text
schema version 23 + dream tables
dream after core sleep creates entry/memory/proactive intent
heartbeat wake auto-runs dream
short nap skips dream by default
doctor warns when completed core sleep lacks dream
```

完整回归：

```text
79 passed
```
