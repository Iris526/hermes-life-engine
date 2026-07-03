# LifeEngine 总设计文档 v0.11.10 — Sleep / Reply / Dream 策略配置与 UX 收敛

Plugin version: `0.11.10`  
Schema version: `30`  
sqlite-vec: required

## 1. 本版本定位

v0.11.10 不新增新的生活大模块，而是把 v0.11.1-v0.11.9 中已经形成的睡眠、回复门禁、梦、自检与分享机制，收敛成一层可读、可调、可解释的策略系统。

目标是：

```text
复杂机制给 Agent 使用。
简单策略给人类理解。
所有策略变更可追踪、可审计、可恢复默认。
```

## 2. 新增 Policy Layer

新增模块：

```text
lifeengine/sleep_reply_dream_policy.py
```

新增表：

```text
sleep_reply_dream_policies
sleep_reply_dream_policy_audits
sleep_reply_dream_policy_suggestions
```

这层管理：

```text
核心睡眠规则
睡眠延迟规则
午睡/补觉触发规则
ReplyGate 行为
call 关键词
不可打断事件 lease
延迟回复摘要模板
DreamRun 规则
DreamAudit 修复偏好
醒来梦分享策略
人类命令面与 Agent 工具面 UX
```

## 3. Policy Presets

v0.11.10 提供预设：

```text
balanced
  默认。适合多数 Agent。

gentle
  更重视睡眠恢复，较早提醒补觉，减少打扰。

night_owl
  夜猫子节律。睡得晚，醒得晚。

workday
  工作日节律。更倾向固定闹钟与早睡。

private
  梦默认不分享，只写 self_journal。

debug
  更详细记录策略行为，便于测试。
```

## 4. Tool / CLI / Slash Surface

新增 Hermes tool：

```text
life_policy
```

支持：

```text
get / status / summary
explain
set / patch / update
preset / profile
reset / defaults
suggest / suggestions / recommend / review
suggestion_list / list_suggestions
audits / history
```

CLI：

```bash
hermes lifeengine policy get
hermes lifeengine policy explain
hermes lifeengine policy preset --preset night_owl
hermes lifeengine policy patch --patch '{"dream":{"share_on_wake":false}}'
hermes lifeengine policy suggestions
hermes lifeengine policy audits
```

Slash：

```text
/life policy
/life policy explain
/life policy suggestions
/life policy preset night_owl
/life policy reset
/life policy audits
```

人类常用命令仍然保持简洁：

```text
/life
/life setup
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

## 5. 策略如何影响现有模块

### 5.1 SleepPlan

`life_sleep plan_day` 在没有显式时间时，会使用 policy：

```text
sleep.bedtime_window
sleep.wake_window
sleep.alarm_policy
```

而不是硬编码默认时间。

### 5.2 ReplyGate

ReplyGate 现在读取 policy：

```text
reply.gate_mode
reply.call_words
reply.sleeping_message_policy
reply.uninterruptible_policy
reply.leases
```

模块 gate 仍然可以覆盖策略：

```text
/life module reply_gate advisory|auto|strict|policy
```

### 5.3 Delayed Reply Digest

释放延迟回复时使用：

```text
reply.delayed_digest.template
reply.delayed_digest.max_items
reply.delayed_digest.style
```

默认模板：

```text
我刚才不方便及时回复时收到了 {count} 条消息，主要是：{summary}
```

### 5.4 Dream Share

DreamEntry 的 share text 现在通过：

```text
dream.share_template
dream.share_mode
dream.share_on_wake
dream.auto_send
```

控制。

梦仍然保持：

```text
truth_layer = dream_symbolic
```

不能作为现实事实证据。

## 6. Context 注入

`pre_llm_call` 的 LifeEngine context 现在包含：

```text
sleep_reply_dream_policy:
  profile
  summary
  agent_rules
```

Agent 会看到类似：

```text
Sleep: target 450 min, bedtime window [...]
ReplyGate: mode=advisory, sleep policy=...
Dream: enabled, share via pending_intent, repair=manual
```

并得到规则：

```text
如果 sleep debt / fatigue 高，优先补觉或低强度任务。
有 delayed replies 时，用 digest 模板聚合回复。
梦是 dream_symbolic，不能当现实事实。
不要把内部策略/audit 信息暴露给用户。
```

## 7. Policy Suggestions

`life_policy suggestions` 会检查近期状态并给出建议：

```text
睡眠压力高 → 建议开启/安排 recovery_sleep
有 pending delayed replies → 建议开启 digest 聚合
梦 auto_send 开启 → 建议改成 pending_intent
```

建议会写入：

```text
sleep_reply_dream_policy_suggestions
```

不会自动改策略。

## 8. Trace / Audit

每次策略变更都写：

```text
sleep_reply_dream_policy_audits
life_journal
```

记录：

```text
old_policy_json
new_policy_json
patch_json
source
updated_by
```

## 9. 当前闭环

v0.11.10 后，Sleep / Reply / Dream 不再只是功能集合，而有了可管理策略层：

```text
Policy preset / patch
  ↓
Policy audit
  ↓
SleepPlan defaults / ReplyGate / Digest / DreamShare
  ↓
Agent context
  ↓
Trace / suggestions
```

## 10. 下一步建议

v0.11.11 建议做：

```text
Sleep / Reply / Dream policy acceptance
真实策略切换场景验收
preset 对 Agent 行为影响的 synthetic tests
策略导出/导入
策略冲突检查
```
