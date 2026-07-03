# LifeEngine 总设计文档 v0.11.19

v0.11.19 是 **Human-Friendly Surface / Schedule Timeline / Required Settings Convergence** 小版本。它不新增新的底层生活大模块，而是把已经完成的 Event V2、Sleep、ReplyGate、Dream、Autonomy、Managed Review 等能力收敛成人类可读、人类少命令、Agent 自主运行的产品面。

```text
Plugin version: 0.11.19
Schema version: 39
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 设计动机

v0.11.18 以前 LifeEngine 的底层能力已经很完整，但人类入口仍然太像调试面：命令太多、review 容易出现过多 pending、部分输出仍偏 JSON。v0.11.19 的目标是把产品面收敛成：

```text
Agent 自己管理自己的生活。
人类只看少量页面。
Agent 工具面保持完整。
所有人类命令默认输出可读文本。
```

## 2. Agent 自主原则

Agent-owned life 不应该要求人类持续审核。默认策略调整为：

```text
autonomy = full
managed_review_loop = auto
final_audit = advisory
schedule_view = human
```

含义：

```text
Autonomy Planner 默认可围绕 Goal / SleepDayState / Resource / Schedule 自主规划。
Managed Review Loop 默认可处理 safe-auto 维护项。
FinalGate 默认只写 advisory 和内部反馈，不把内部审计文本暴露给用户。
User-owned facts 仍然需要 confirmation。
```

## 3. 人类命令面

普通人只需要：

```text
/life                         状态页
/life setup <设定>             设定草案
/life commit                  提交设定
/life pause                   暂停 LifeEngine
/life resume                  恢复运行
/life run                     手动 heartbeat
/life schedule [范围/日期]      看日程；默认今天
/life review                  看人类待处理/建议项
/life config                  必选设定检查
/life call                    强制唤醒/打断/恢复
/life doctor                  健康检查
/life backup                  备份
/life advanced                高级命令
```

复杂能力仍然暴露给 Agent：`life_commit`, `life_event`, `life_resource`, `life_sleep`, `life_dream`, `life_review`, `life_execution`, `life_truth` 等。

## 4. `/life schedule`

新增 human schedule view：

```text
/life schedule                 默认今天
/life schedule today
/life schedule tomorrow
/life schedule week
/life schedule 2026-06-11
```

Schedule 展示 ScheduleBlock，而不是把 schedule 等同于 Event：

```text
Event = 生活里要做的事情。
ScheduleBlock = 给 Event/Action 预留或实际占用的时间块。
SleepPlan/SleepSession = 睡眠计划与实际睡眠。
```

输出示例：

```text
今天的日程（2026-06-11）
=================
1. 06-11 10:30 - 06-11 12:00  处理小单子
   类型：work；时间块：planned；事件：scheduled
   实际：尚未开始
   中断：soft_interruptible
```

对于睡眠：

```text
计划：23:50 - 07:30 核心睡眠
实际：02:10 - 06:40，被用户 call 叫醒
结果：sleep debt +160min，次日建议补觉
```

## 5. `/life review`

Review 改为人类 item list，每一行体现：

```text
什么时候 / 最近
严重程度
做了什么或需要处理什么
建议工具/动作
```

默认不再把这些作为人类 pending：

```text
FinalGate 内部反馈
managed review acceptance/stress 诊断项
纯调试 trace
```

这些仍写入 trace/audit/feedback queue，供 Agent 和开发者查看。

## 6. `/life config`

LifeEngine 必选设定检查包括：

```text
identity/persona        Agent 是谁
worldview               Agent 生活在哪种世界
time                    时区、时间流速、时钟真相源
weather                 天气规则或真实来源绑定
truth_sources           现实/虚拟真相源绑定
sleep                   核心睡眠规则、醒来策略
resources               可计数资源与初始值
autonomy                Agent 自主运行策略
```

支持两类设定：

```text
Virtual rules:
  天气随机、虚构城市、虚拟货币、自定义资源。

Real bindings:
  system_clock、用户所在地天气、外部工具观察、固定城市、真实货币参考。
```

所有设定检查和提交结果默认输出人类可读文本。

## 7. 启动检查

`on_session_start` 会运行 startup check：

```text
1. 检查必选设定是否缺失。
2. 对 Agent 自己的生活启用 autonomy=full。
3. 对 Agent 自己的 safe maintenance 启用 managed_review_loop=auto。
4. 不自动修改用户侧事实。
5. 写 required setting check 记录。
```

## 8. 不变量

v0.11.19 继续保持之前的闭环：

```text
所有 durable mutation 走 LifeOps。
所有 LifeOps 走 Validator / Transaction / Journal / Receipt。
Agent-owned life 可以自主推进。
User-owned facts 必须 confirmation。
Schedule 是时间块，Event 是生活事项。
Sleep 计划和实际睡眠分离。
Dream 是 dream_symbolic，不污染现实事实。
Review 自动处理只允许 safe-auto 项。
人类默认看可读页面，不看 JSON。
```

## 9. v0.11.19 的边界

v0.11.19 是产品面收敛版本，不改变 Hermes core。插件仍通过：

```text
pre_llm_call
transform_llm_output
pre_gateway_dispatch
Hermes tools
slash command
CLI command
```

接入 Hermes。严格同轮 FinalGate repair loop 仍需要未来可选 Hermes core patch。

## 10. 下一步建议

进入 v0.12 之前，可以继续做：

```text
1. /life schedule 的 richer filtering：按类别、状态、sleep/work/study。
2. Human setup wizard：一步步补齐缺失设定。
3. Review 与 Schedule 的自然语言解释增强。
4. Agent tool guide freeze。
5. v0.12 stabilization / API freeze。
```
