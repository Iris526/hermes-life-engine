# LifeEngine 总设计文档 v0.9.3

> **v0.9.3 主题：FinalGate Repair UX / Final Answer Audit Hardening。**

v0.9.3 不新增新的生活行为能力。它强化 LifeEngine 的最终出口：当模型在最终回答里说出尚未提交、缺少证据的生活事实时，系统不只是硬拦截，而是生成可解释报告、修复建议和可追踪的 LifeOps 草案。

---

## 1. 版本定位

LifeEngine 的核心不变量仍然是：

```text
final_claims ⊆ canonical_state ∪ current_turn_commit_receipts
```

也就是说，Agent 最终说出口的生活事实，必须来自：

```text
1. 已存在的 canonical life state；或
2. 本轮已经提交成功的 CommitReceipt facts。
```

v0.9.3 做的是让这个不变量更可用、更可解释、更适合进入 v1.0 前稳定化。

---

## 2. FinalGate 的职责

FinalGate 只负责最终回答出口，不负责自动创造生活事实。

它执行：

```text
1. detect_life_claims
   从最终回答中检测 durable life claims。

2. evaluate_final_response
   将 claim 与本轮 CommitReceipt facts 和已有 canonical facts 匹配。

3. write_final_gate_report
   对 unsupported claims 写入 final_gate_reports。

4. build_repair_message
   根据 strict / repair / trace 模式生成用户可读替换文本。

5. audit_final_output
   通过 Hermes transform_llm_output hook 在交付前替换或放行。
```

FinalGate 不会自动提交建议 LifeOps。建议永远只是草案，仍需经过：

```text
life_commit / LifeOps-backed mutation tool
  ↓
Validator
  ↓
Transaction
  ↓
Journal
  ↓
CommitReceipt
```

---

## 3. 新增 Schema：final_gate_reports

Schema version 提升到 13。

新增表：

```text
final_gate_reports
```

字段：

```text
id
owner_kind
owner_id
session_id
turn_id
trace_id
mode
status
response_preview
claims_json
unsupported_json
supported_json
suggested_ops_json
repair_json
created_at
```

用途：

```text
/life final_gate reports
/life final_gate get <report_id>
hermes lifeengine final_gate reports
hermes lifeengine final_gate get --report-id <report_id>
```

报告内容包括：

```text
检测到的 claim
已支持的 claim
未支持的 claim
证据样本
本轮 receipt fact 数量
canonical fact 数量
建议 LifeOps 草案
拦截 / 修复 / trace 状态
```

---

## 4. FinalGate 模式

### 4.1 strict

默认模式。发现 unsupported life claim 时阻断最终回答，并返回：

```text
LifeEngine 拦截原因
缺少证据的 claim 列表
建议 LifeOps 草案
查看 trace/report 的命令
```

### 4.2 repair

软修复模式。发现 unsupported claim 时返回更自然的修正文案：

```text
我先修正一下：刚才那段回复里有些生活事实还没有通过 LifeEngine 提交……
```

适合用户体验优先的 Agent。

### 4.3 trace

只记录、不阻断。用于调试和低风险实验。

```text
status = traced
最终回答保持原样
report 仍然写入 final_gate_reports
```

---

## 5. Tool / CLI / Slash

新增或强化工具：

```text
life_final_gate
```

支持：

```text
check
reports
get
```

Slash：

```text
/life final_gate check <text>
/life final_gate reports
/life final_gate get <report_id>
```

CLI：

```bash
hermes lifeengine final_gate check --response-text "我今天中午吃了咖喱饭。"
hermes lifeengine final_gate reports
hermes lifeengine final_gate get --report-id <report_id>
```

---

## 6. Repair UX

当最终回答中包含 unsupported life claims 时，FinalGate 现在会给出：

```text
1. 哪些内容缺少 CommitReceipt 或 canonical state 证据；
2. 哪些内容当前可以确认；
3. 可以如何修复；
4. 建议 LifeOps 草案；
5. 报告 ID 和 trace 查询方式。
```

示例修复流：

```text
模型最终回答：
  我今天中午吃了咖喱饭。

FinalGate 检测：
  “我今天中午吃了咖喱饭” 是生活事实 claim。
  本轮没有 receipt。
  canonical state 也没有对应 meal/event。

FinalGate 输出：
  拦截 / 修正说明
  suggested_ops:
    CREATE_EVENT meal completed source=agent_retro_assertion

后续正确做法：
  模型先调用 life_commit 提交该 LifeOp。
  成功后重新回答。
```

---

## 7. 与 Hermes Hook 的关系

LifeEngine 仍然通过 Hermes 插件机制接入：

```text
pre_llm_call
transform_llm_output
registered tools
slash command
CLI command
```

`transform_llm_output` 是最终回复交付前的替换点，因此插件版可以在工程上阻断未提交生活事实。

当前边界仍然存在：Hermes 会隔离插件 hook 异常，单个插件异常不会打断宿主主循环。因此形式化 fail-closed 仍建议未来增加一个宿主级 mandatory final gate patch。

---

## 8. 当前闭环状态

截至 v0.9.3：

```text
Canon 设定闭环：已实现
Pause / setup 防污染闭环：已实现
LifeOps mutation 闭环：已实现
CommitReceipt / FinalGate evidence 闭环：已实现
FinalGate repair reports / suggested LifeOps / soft-repair UX：已实现
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
Lifecycle validator：已实现
Doctor / invariant checks：已实现
Install / upgrade / backup / rebuild / heartbeat script diagnostics：已实现
```

---

## 9. v0.9.3 验收范围

测试覆盖：

```text
1. strict mode 返回拦截 UX 并写 final_gate_reports。
2. trace mode 记录 report 但不阻断。
3. repair mode 返回软修正文案。
4. life_final_gate tool 可 check/report/get。
5. /life final_gate slash command 可用。
6. hermes lifeengine final_gate CLI surface 可用。
7. schema version = 13，并创建 final_gate_reports。
8. 完整回归测试通过。
```

---

## 10. 下一步建议：v0.9.4

继续小版本迭代，不直接冲 v1.0。

建议 v0.9.4 做：

```text
DB export / import / restore
FTS5 rebuild 验收
sqlite-vec rebuild 验收
profile migration
package manifest / checksum
large DB maintenance smoke tests
```

v0.9.3 让最终出口更可解释；v0.9.4 应该让长期存储更可维护。
