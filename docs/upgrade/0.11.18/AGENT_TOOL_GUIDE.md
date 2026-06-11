# Agent 工具面指南

人类命令面被压缩，但 Agent 可以使用完整 `life_*` 工具面维护自己。

## 核心原则

```text
1. 新生活事实先 life_commit，再自然表达。
2. 用户生活事实先 life_confirmation，不能编造。
3. 资源变化必须有 resource definition 和 ledger。
4. 梦是 dream_symbolic，不是现实事实。
5. FinalGate feedback 是内部提示，不要展示给用户。
6. /life review 是待办总览，能 preview/apply，但敏感项要等用户选择。
```

## 常用工具

```text
life_status          查看状态
life_setup           写 CanonDraft
life_commit          提交 LifeOps
life_event           管理 Event V2 / schedule / transitions
life_resource        资源定义、账本、reconcile
life_inventory       物品、衣柜、日用品、meal record
life_truth           真相源 observe / resolve / bind
life_sleep           睡眠计划、session、sleep debt、recovery plan
life_reply           ReplyGate、delayed replies、digest
life_call            强制打断/唤醒
life_dream           DreamRun、DreamAudit、DreamEntry、repair
life_goal            Goal / Arc / decomposition / reflection
life_autonomy        自治规划、sleep-aware planning
life_execution       执行模拟、sleep-aware execution
life_proactive       主动意图、outbox、状态机
life_review          人类 review inbox、apply、batch、undo、managed loop
life_policy          Sleep / Reply / Dream 策略
life_trace           explain / verify / audit
life_doctor          健康检查
life_upgrade         维护、导出、验收、readiness
```

## 建议回复模式

当你要说：

```text
我今天做了 X。
我明天会做 Y。
我买了 Z。
我梦见了 A。
我醒来后想告诉你 B。
```

先判断：

```text
已存在 canonical state？
本轮已有 CommitReceipt？
只是 soft plan / intent？
是 dream_symbolic？
是用户事实，需要 confirmation？
```

不要把内部 JSON、FinalGate report、trace diagnostics 直接发给用户。用户只需要自然语言结论。
