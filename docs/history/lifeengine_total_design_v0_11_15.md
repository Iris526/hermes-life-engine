# LifeEngine 总设计文档 v0.11.15

v0.11.15 是 **Review Undo / Rollback Trace** 小版本。它不新增新的 Agent 生活行为能力，而是在 v0.11.14 的 `/life review apply` 与 `/life review apply_all` 基础上补上可审计的撤销层。

```text
Plugin version: 0.11.15
Schema version: 35
sqlite-vec: required
Hermes integration: directory plugin, no core-loop fork
```

## 1. 设计目标

v0.11.13 让单个 review item 可以执行，v0.11.14 让安全 review item 可以批量执行。v0.11.15 补上发布前必须有的逆向链路：

```text
review action / batch apply
  ↓
human_review_action_runs / human_review_batch_runs
  ↓
undo preview
  ↓
undo apply
  ↓
human_review_undo_runs / human_review_undo_items
  ↓
journal / audit trace
```

Undo 不是任意时间旅行，也不是数据库回滚。它是一个**保守、显式、可追踪的补偿操作层**。

## 2. 支持范围

默认支持安全撤销：

```text
1. delayed_reply release
   已释放的 delayed replies 可以重新打开为 pending；对应 digest 标记为 undone。

2. recovery_sleep plan
   由 review sleep_state 建议创建、尚未开始的 recovery sleep plan 可以取消；关联 sleep_plan、recovery_plan、event、schedule block、wake jobs 会被取消或标记。

3. noop / policy suggestions / diagnostic review
   如果没有实际改变生活状态，undo 是 noop，但仍写 trace。
```

默认不自动撤销：

```text
user_confirmation confirm
proactive send / suppress
policy patch
DreamAudit repair that changed unrelated schedule/resource state
arbitrary LifeOps transaction
```

这些需要显式 corrective LifeOps、备份恢复，或人工 trace 审计。

## 3. 新增表

```text
human_review_undo_runs
human_review_undo_items
```

并给已有表增加：

```text
human_review_action_runs.undo_status
human_review_action_runs.undo_run_id
human_review_action_runs.undo_plan_json

human_review_batch_runs.undo_status
human_review_batch_runs.undo_run_id
human_review_batch_runs.undo_plan_json
```

## 4. 新增命令

Slash command：

```text
/life review undo_preview <action_run_id>
/life review undo <action_run_id>
/life review batch_undo_preview <batch_run_id>
/life review batch_undo <batch_run_id>
/life review undo_runs
/life review get_undo <undo_run_id>
```

CLI：

```bash
hermes lifeengine review undo_preview <action_run_id>
hermes lifeengine review undo <action_run_id>
hermes lifeengine review batch_undo_preview <batch_run_id>
hermes lifeengine review batch_undo <batch_run_id>
hermes lifeengine review undo_runs
hermes lifeengine review get_undo <undo_run_id>
```

Tool surface：

```text
life_review(action="undo_preview")
life_review(action="undo")
life_review(action="batch_undo_preview")
life_review(action="batch_undo")
life_review(action="undo_runs")
life_review(action="get_undo")
```

## 5. 安全规则

```text
1. undo_preview 永远不改状态。
2. undo 只执行 safe_undo=true 的计划。
3. batch_undo 按 batch item 的反向顺序撤销。
4. 不支持的 action 会写 unsupported undo_run，不会猜测修复。
5. undo 只改变 review UX 产生的安全维护状态，不伪造生活事实。
6. 所有 undo 都写 audit 和 journal。
```

## 6. 当前闭环

截至 v0.11.15：

```text
Human Review aggregation: done
Single review action application: done
Safe batch apply: done
Undo preview/apply: done
Batch undo preview/apply: done
Undo trace: done
```

下一步建议：v0.11.16 做 **Review Managed Autonomy**，即在策略允许时让 Agent 在 heartbeat 后自动 preview/apply safe review items，并受 daily limit、failure budget、section allowlist 约束。
