# LifeEngine v0.11.9 总设计增量：Sleep / Reply / Dream 真实对话验收

## 版本

- Plugin version: `0.11.9`
- Schema version: `29`
- sqlite-vec: required
- Hermes integration: directory plugin, no core-loop fork

## 本版目标

v0.11.9 不增加新的生活概念模块，而是把 v0.11.1-v0.11.8 的睡眠、回复门禁、梦、自治和执行链路，整理成一组**用户可感知的真实对话场景验收**。

此前 v0.11.8 已经验证：

```text
SleepDayState / sleep debt / all-nighter
  → Autonomy Planner
  → recovery sleep / downshifted goal step
  → Execution Simulator
  → partial / postponed / rescheduled
  → Trace explain
```

v0.11.9 增加用户消息参与后的链路：

```text
用户聊天延迟入睡
  → 实际睡眠与计划睡眠不一致
  → 用户 call 打断睡眠
  → delayed replies 聚合释放
  → 醒来 DreamRun 生成梦与分享意图
  → DreamAudit 修复漏结算状态
  → 睡眠不足影响次日执行
  → trace explain 可解释整条链路
```

## 新增表

```text
sleep_reply_dream_conversation_acceptance_runs
sleep_reply_dream_conversation_acceptance_scenarios
```

这些表只存验收元数据，不创建真实 Agent 生活事实。

## 验收隔离策略

验收 runner 使用 synthetic owner：

```text
<real_owner_id>-crd-<acceptance_run_id>
```

所以它会在同一个嵌入式 SQLite DB 中留下可查 trace，但不会污染真实 Agent 的生活状态、资源、记忆、睡眠和梦。

## 新增验收场景

```text
CRD01_CHAT_DELAYS_SLEEP_ACTUAL_DIFFERS
  用户聊天让 Agent 晚于计划时间入睡，SleepSession.actual_sleep_at 与 SleepPlan.planned_sleep_at 不一致。

CRD02_CALL_WAKES_SLEEP_AND_RELEASES_DIGEST
  life_call 在 Agent 睡眠中强制唤醒，释放 delayed replies，并生成 delayed_reply_digest。

CRD03_WAKE_DREAM_SHARE_INTENT
  Agent 醒来后运行 DreamRun，创建 DreamEntry、dream memory 和可分享 ProactiveIntent。

CRD04_DREAM_AUDIT_REPAIR_AND_WAKE_REPLY
  DreamAudit 发现过期 schedule block 和 pending delayed replies，repair 通过 LifeOps 提交修复。

CRD05_INTERRUPTED_SLEEP_AFFECTS_NEXT_EXECUTION
  被 call 打断的短睡眠被记录为 SleepDayState，并影响次日重要工作事件执行结果。

CRD06_TRACE_COVERS_USER_VISIBLE_CHAIN
  trace explain 能覆盖事件状态流转、DreamEntry、delayed reply digest 和 execution sleep adjustment。
```

## 新增工具入口

通过 `life_upgrade`：

```json
{"action":"sleep_reply_dream_conversation_acceptance"}
```

别名：

```text
crd_acceptance
conversation_acceptance
srd_conversation_acceptance
```

查询：

```json
{"action":"sleep_reply_dream_conversation_acceptance_runs"}
{"action":"sleep_reply_dream_conversation_acceptance_get", "acceptance_run_id":"..."}
```

## CLI / Slash

CLI：

```bash
hermes lifeengine upgrade crd_acceptance
hermes lifeengine upgrade crd_acceptance_runs
hermes lifeengine upgrade crd_acceptance_get --acceptance-run-id <run_id>
```

Slash：

```text
/life upgrade crd_acceptance
/life upgrade crd_acceptance_runs
/life upgrade crd_acceptance_get <run_id>
```

## 闭环判断

v0.11.9 后，Sleep / Reply / Dream / Execution 用户可感知链路已进入验收级闭环：

```text
计划睡眠
  → 用户聊天导致实际入睡延迟
  → 睡眠中普通消息可延迟
  → call 可强制唤醒
  → delayed replies 可聚合释放
  → 醒来可做 DreamRun / DreamAudit
  → 梦可生成 share intent
  → DreamAudit 可通过 LifeOps 修复漏结算
  → 睡眠不足影响次日执行
  → trace explain 可解释
```

## 下一步建议

v0.11.10 建议进入 Sleep / Reply / Dream 的策略配置与 UX 收敛：

```text
1. Sleep Canon policy UI/commands：核心睡眠、午睡、通宵容忍度、叫醒规则。
2. ReplyGate policy UX：advisory/auto/strict 的用户可读解释。
3. Dream sharing policy：醒来自动提起 / pending_only / 不分享。
4. delayed reply digest 的自然语言模板。
5. sleep/reply/dream doctor 的一键恢复建议。
```
