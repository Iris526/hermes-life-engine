# LifeEngine 总设计文档 v0.9.5

> **v0.9.5 主题：Human UX Simplification + Advisory FinalGate。**

这版不是扩展 Agent 生活能力，而是根据真实试用反馈修正两个关键体验问题：

1. 人类命令太多，记不住。
2. FinalGate 对用户可见地硬拦截，并且把计划/意图误判成未提交事实。

v0.9.5 的原则是：

```text
Human surface small.
Agent tool surface full.
FinalGate advises the Agent, not the user.
Soft plans are not hard unsupported facts.
```

---

## 1. 人类命令面收敛

LifeEngine 的内部工具很多，这是必要的，因为 Agent 需要精细操作：

```text
life_commit
life_resource
life_event
life_truth
life_inventory
life_goal
life_autonomy
life_proactive
life_execution
life_trace
...
```

但人类不应该记这些。v0.9.5 把普通人类入口收敛成：

```text
/life                查看状态
/life help           简短帮助
/life setup <text>   设定世界观/人设/规则，只写 CanonDraft
/life commit         提交设定草案
/life pause          暂停 LifeEngine，禁止生活 mutation
/life resume         恢复运行
/life run            手动 heartbeat tick
/life review         查看待确认、主动消息、FinalGate 提醒
/life doctor         健康检查
/life backup         导出 profile 备份
/life advanced       高级命令列表
```

高级命令仍然存在，但隐藏到 `/life advanced`。这满足：

```text
用户可以简单使用。
Agent 可以完整自治。
开发者可以调试全部模块。
```

---

## 2. FinalGate 默认从 strict 改成 advisory

之前 FinalGate 逻辑是：

```text
发现 unsupported life claim
  ↓
替换最终回复
  ↓
把“拦截原因 + 建议 LifeOps”直接发给用户
```

这在真实对话里不合理。用户看到的是内部 gate 诊断，而不是 Agent 的回复。

v0.9.5 改为：

```text
发现 unsupported hard claim
  ↓
写 final_gate_reports
  ↓
写 audit_log
  ↓
写 internal final_gate_feedback_queue
  ↓
本次用户可见回复放行
  ↓
下一轮 pre_llm_call 把 feedback 注入给 Agent
```

也就是说：

> FinalGate 的主要对象是 Agent，不是用户。

Agent 下一轮会看到：

```text
internal_final_gate_feedback:
  上一轮有某些生活 claim 缺少证据。
  不要把这个诊断展示给用户。
  需要时先提交 LifeOps，或者把它改成想法/计划/草案。
```

---

## 3. Claim 分类：hard vs soft

v0.9.5 把最终回答里的生活 claim 分成两类。

### hard claim

需要 evidence 或 receipt，例如：

```text
我今天中午吃了咖喱饭。
我已经买了一条裙子。
我花了 8200 日元。
我的衣柜里有一条藏青色百褶裙。
这个事件已经完成了。
```

hard claim 如果没有 evidence，会进入 advisory report。

### soft claim

不应被硬拦截，例如：

```text
我今天要好好做。
今天安排大概是……
我准备上午去处理一下。
我可能下午买点小甜水。
我打算把这个委托做完。
```

这些是计划、意图、语气、草案、答复结构。它们可以提示 Agent 是否要落库，但不应该对用户硬拦截。

---

## 4. FinalGate modes

默认：

```text
final_audit = advisory
```

行为：

```text
写报告 + 写内部 feedback + 放行用户回复
```

高级模式：

```text
off      完全关闭
advisory 默认，内部提示，不拦截
trace    advisory alias
warn     放行原文，并追加极短 warning
repair   替换为保守修正文案
strict   旧式硬拦截，仅供调试/强一致实验
```

v0.9.5 还保留 gate intervention budget：同一 session/turn 最多 3 次 intervention。超过后自动放行并写 trace，避免无限拦截循环。

---

## 5. 新增 Schema：final_gate_feedback_queue

Schema version 提升到 15。

新增表：

```text
final_gate_feedback_queue
```

字段：

```text
id
owner_kind
owner_id
session_id
turn_id
report_id
source
status
message
created_at
delivered_at
```

用途：

```text
final audit 阶段生成内部反馈
pre_llm_call 阶段注入给 Agent
注入后标记 delivered
```

这保证 gate 不把内部错误直接发给用户，又能继续指导 Agent 修正未来行为。

---

## 6. Hermes 纯插件边界

当前 Hermes 插件的 `transform_llm_output` 能替换最终回复，但不能把 draft 返还给模型并在同一 turn 重新生成。v0.9.5 因此采用纯插件可实现的稳定策略：

```text
默认 advisory pass-through
下一轮内部 feedback
```

如果未来给 Hermes 增加 mandatory final gate / repair-loop core patch，则可以把同一 turn 的修复做成：

```text
draft
  ↓
FinalGate feedback
  ↓
model repair attempt
  ↓ max 3
final pass
```

但在无 core patch 的版本里，不应向用户暴露原始 gate 诊断。

---

## 7. 当前 v0.9.5 闭环状态

```text
Core life closure: 已完成
Trace / audit closure: 已完成
FinalGate advisory feedback closure: 已完成
Human command simplification: 已完成
Agent full tool autonomy surface: 已保留
```

v0.9.5 修复的是发布前非常重要的产品体验问题：

> LifeEngine 应该让 Agent 更像活人，而不是让用户看到数据库审计器。
