# LifeEngine 设计哲学

LifeEngine 的目标不是做一个“记忆插件”，而是给 Agent 一个可审计、可暂停、可迁移、可追踪、可自我修复的生活运行时。

## 1. Canon First

Agent 的世界观、人设、资源定义、真相源绑定、睡眠/回复/梦策略，是最高生活真相源。普通记忆和模型临场生成不能覆盖 Life Canon。

```text
Platform Policy
  ↓
Life Canon
  ↓
Truth Source Bindings
  ↓
Committed Life State
  ↓
Conversation Context
  ↓
Model Proposal
```

## 2. State Before Narrative

Agent 可以叙事，但叙事不能凭空污染生活。新的生活事实必须先通过 LifeOps、Validator、Transaction、Journal、CommitReceipt，之后才能成为可引用事实。

## 3. 资源守恒

资源不只是钱。时间、精力、专注、睡眠债、心情、灵感、物品、关系、权限都可以登记为资源。资源变化必须进入 ledger 或 entity movement。

## 4. Pause Means No Mutation

暂停态和设定态不能推进生活、不能写事件、不能 retro-generate 过去。用户重构世界观时，LifeEngine 只写 CanonDraft，不污染生活流水。

## 5. Embedded, Not External

LifeEngine 是嵌入式插件：SQLite + sqlite-vec，本地自循环，不依赖外部向量服务。Hermes 只是第一个 adapter，核心设计可迁移到其他 Agent Loop。

## 6. Trace Everything

所有关键行为都要有 trace：LifeOps、Journal、Receipt、Doctor、FinalGate、DreamAudit、ReplyGate、Managed Review 都要能解释“为什么发生”。

## 7. Advisory Gate, Not User-Facing Punishment

FinalGate 默认不应该把内部审计错误发给用户。它默认 advisory：写报告、给 Agent 内部反馈、进入 review；只有显式 strict/repair 才干预输出。

## 8. Human Surface Small, Agent Surface Full

人类只需要少量命令。复杂工具面给 Agent 自己使用，让 Agent 学会维护自己的 LifeEngine。

## 9. Sleep Is Real State

睡眠不是一段静态日程，而是有计划、实际、打断、梦、自检、恢复资源和第二天影响的状态系统。

## 10. Dream Is Symbolic, Not Reality

梦可以整理记忆、反映状态、产生分享意图，但梦的 truth layer 是 `dream_symbolic`。梦不能证明现实事实，也不能直接改写资源、外部真相或用户事实。

## 11. Review Inbox Is the Human Control Room

`/life review` 是人类入口。它把睡眠债、延迟回复、梦分享、策略冲突、FinalGate advisory、Doctor warning、Proactive outbox 聚合成一页，并支持安全建议执行、批量执行、undo 和 managed loop。

## 12. Autonomy Is Policy-Gated

Agent 可以自发规划、处理 review、评估 proactive intent，但必须受 Canon、资源、策略、daily limit、failure budget 和 trace 约束。
