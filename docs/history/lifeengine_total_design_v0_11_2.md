# LifeEngine 总设计文档 v0.11.2 — ReplyGate / delayed replies / life_call

> v0.11.2 建立在 v0.11.1 的 SleepPlan / SleepSession 之上，补上“用户消息进入时，Agent 是否应该立刻回复”的运行时门禁。它不是最终回复审计 FinalGate，而是 **incoming message gate**：在 Agent 睡觉、做不可打断事件、等待统一回复时，决定放行、延迟、叫醒或强制打断。

```text
Plugin version: 0.11.2
Schema version: 22
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 为什么需要 ReplyGate

LifeEngine v0.11.1 已经能表示：

```text
Agent 计划睡觉
Agent 实际入睡
Agent 实际醒来
睡眠和计划不一致
睡眠被打断
realtime_state 标记 asleep/napping
```

但还缺一个关键闭环：

```text
用户发消息时，Agent 当前是否能被打断？
如果睡着了，要不要醒来？
如果在不可打断事件里，要不要延迟回复？
如果系统卡住，怎么强制唤醒？
```

v0.11.2 引入 ReplyGate：

```text
incoming user message
  ↓
ReplyGate reads agent_realtime_state
  ↓
allow / advisory / defer / call_override
  ↓
optional delayed_replies queue
  ↓
optional life_call emergency release
  ↓
trace + journal + receipt
```

## 2. ReplyGate 和 FinalGate 的区别

```text
ReplyGate:
  入口门。
  在用户消息进入 Agent 之前或进入时判断是否应当立即回复。

FinalGate:
  出口门。
  检查 Agent 最终回复里的生活事实有没有 commit evidence。
```

v0.11.2 只新增 ReplyGate，不改变 v0.10.0 之后 FinalGate 的默认 advisory 设计。

## 3. 新增数据表

### reply_gate_decisions

记录每一次门禁判断：

```text
id
owner_kind / owner_id
session_id / turn_id / user_id
incoming_message_preview
decision
reason
mode
active_event_id
active_schedule_block_id
active_sleep_session_id
interruptibility_level
reply_mode
state_snapshot_json
policy_json
trace_id
source
created_at
```

### delayed_replies

记录被延迟的用户消息：

```text
id
owner_kind / owner_id
user_id
session_id / turn_id
message_text
message_preview
gate_decision_id
reason
status: pending / released / cancelled / expired
queued_at
released_at
release_reason
expires_at / expires_at_ts
metadata_json
```

### call_overrides

记录强制唤醒/打断：

```text
id
owner_kind / owner_id
user_id
session_id / turn_id
reason
target_kind
target_id
interrupted_sleep_session_id
interrupted_event_id
gate_decision_id
result_json
created_at
```

### reply_gate_recoveries

Doctor / recovery 诊断记录。

## 4. ReplyGate 决策

```text
allow:
  Agent 可立即回复。

advisory:
  Agent 当前睡觉/忙碌，但 module gate 不是 auto/strict，因此只给 Agent 内部提示，不阻断。

defer:
  Agent 在睡眠或不可打断事件中，并且 reply_gate=auto/strict。消息进入 delayed_replies。

call_override:
  用户显式 call / 紧急 / wake，或 life_call 被调用。强制唤醒或打断。

fail_safe_allow:
  lease 过期或状态异常时，系统放行，防止无限不可打断。
```

## 5. Module Gate

新增：

```json
"reply_gate": "advisory"
```

推荐默认是 advisory。

```text
off:
  不运行 ReplyGate。

advisory:
  记录判断和内部提示，不阻断用户消息。

auto:
  睡眠/不可打断状态下，普通消息被延迟。

strict:
  和 auto 类似，但更适合测试/受控场景。
```

## 6. life_reply 工具

新增 Hermes tool：

```text
life_reply
```

支持：

```text
status / state
assess / gate / check
defer / queue
release / release_pending
list / delayed / queue_list
calls / call_overrides
doctor
call / override / wake
```

示例：

```json
{
  "action": "assess",
  "message_text": "你醒着吗？"
}
```

```json
{
  "action": "release",
  "reason": "Agent is available again"
}
```

## 7. life_call 工具

新增 Hermes tool：

```text
life_call
```

语义：

```text
无论 Agent 在睡觉、不可打断事件、等待回复、状态异常，都强制唤醒/打断。
```

它会：

```text
1. 写 reply_gate_decisions
2. 如果在睡眠中，写 sleep_interruptions 并 wake sleep session
3. 如果在不可打断事件中，尝试把 event 标记 partial
4. realtime_state → in_conversation / immediate
5. release delayed_replies
6. 写 call_overrides / life_journal / CommitReceipt
```

## 8. Hermes gateway hook

v0.11.2 注册：

```text
pre_gateway_dispatch
```

在 gateway 场景中：

```text
reply_gate=advisory:
  只记录，不拦截。

reply_gate=auto/strict + sleeping/uninterruptible:
  delayed_replies 写入，返回 skip。

call-like message:
  执行 call_override，放行。
```

CLI 场景没有 gateway pre-dispatch，LifeEngine 会在 pre_llm_call context 里注入 ReplyGate 状态，由 Agent 自己按工具协议处理。

## 9. 实时状态和租约

ReplyGate 依赖：

```text
agent_realtime_state.mode
agent_realtime_state.reply_mode
agent_realtime_state.interruptibility_level
agent_realtime_state.active_sleep_session_id
agent_realtime_state.active_event_id
agent_realtime_state.lease_expires_at_ts
```

如果 lease 过期，ReplyGate fail-safe allow，避免：

```text
无限睡眠
无限不可打断
无限 delayed reply
```

## 10. Heartbeat 集成

Heartbeat 在 Agent 变回 available 状态后会尝试：

```text
release_delayed_replies(reason="released by heartbeat after agent became available")
```

这为“事件结束后统一回复”提供了状态基础。具体消息发送仍交给 Agent / gateway adapter / proactive outbox。

## 11. 新增人类命令

简单命令面增加：

```text
/life call
```

高级命令：

```text
/life reply status
/life reply list
/life reply release
/life reply doctor
/life reply assess <message>
```

CLI：

```bash
hermes lifeengine call --reason "urgent"
hermes lifeengine reply status
hermes lifeengine reply release
hermes lifeengine reply doctor
```

## 12. 当前边界

v0.11.2 实现的是 ReplyGate 数据层、工具层、gateway pre-dispatch 和 call override。

还未实现：

```text
DreamRun
DreamAudit
醒来自动分享梦
gateway 真实平台主动 delivery adapter
复杂自然语言紧急程度分类
```

这些留给后续 v0.11.3 Dream 系统与 gateway delivery 整合。

## 13. 验收点

v0.11.2 新增测试覆盖：

```text
schema version 22 + reply tables
advisory 模式下睡眠不硬拦
reply_gate=auto 时睡眠消息进入 delayed_replies
life_call 会唤醒睡眠并释放 delayed replies
不可打断 event 可 defer，并可被 life_call 打断
```

回归测试按文件拆分通过：

```text
74 passed
```
