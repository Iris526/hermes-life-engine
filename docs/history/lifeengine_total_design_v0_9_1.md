# LifeEngine 总设计文档 v0.9.1

> **v0.9.1 主题：Pre-1.0 Hardening 小版本线。**
>
> v0.9 已经实现 Narrative Execution Simulator / Serendipity Engine。v0.9.1 不继续扩新功能，而是进入 v1.0 前的小版本硬化：集中补强生命周期校验、doctor 自检、schema 迁移、包体卫生和 trace/health 可观测性。

---

## 1. 小版本线原则

v1.0 前不再一次性堆大模块，而是用小版本把可发布性打磨稳：

```text
v0.9.1  Lifecycle + Doctor + migration hardening
v0.9.2  Trace explain / evidence rendering polish
v0.9.3  CLI / slash command E2E coverage
v0.9.4  Install / upgrade / rollback testing
v0.9.5  Optional Hermes mandatory final-gate patch proposal
v1.0    Stable MVP release candidate
```

这条线的目标不是“更多能力”，而是：

```text
1. 写入路径更难绕过 LifeOps。
2. 状态机错误更早被 validator 拦截。
3. 安装后可以一键 doctor。
4. DB 迁移可检测。
5. 包体不携带 __pycache__ / .pytest_cache。
6. 进入 v1.0 前每个闭环都有健康检查。
```

---

## 2. v0.9.1 新增设计

### 2.1 Lifecycle Policy 集中化

新增 `lifeengine/lifecycle.py`，集中定义：

```text
Event statuses
Event allowed transitions
Schedule block statuses
Schedule block allowed transitions
Terminal statuses
Schedulable event statuses
Completable event statuses
```

Validator 现在会在 LifeTransaction 创建前校验：

```text
UPDATE_EVENT_STATUS 是否符合事件状态机
COMPLETE_EVENT 是否来自可完成状态
CREATE_SCHEDULE_BLOCK 是否引用可排期事件
UPDATE_SCHEDULE_BLOCK_STATUS 是否符合 schedule 状态机
```

这使错误尽量发生在：

```text
LifeOps validation 阶段
```

而不是：

```text
已经创建 tx/op 后，低层 writer 才抛错
```

### 2.2 Doctor / Invariant Checks

新增/强化 doctor：

```text
rt.doctor()
/life doctor
/life trace doctor
hermes lifeengine doctor
hermes lifeengine trace doctor
```

Doctor 检查：

```text
sqlite-vec 是否加载
PRAGMA user_version 是否等于当前 schema version
关键表是否存在
control/canon 状态是否合理
final_audit gate 是否开启
heartbeat/autonomy gate 是否冲突
journal hash chain 是否完整
resource account 是否与 ledger sum 一致
wake_jobs 是否卡在 running
proactive queue 是否异常堆积
package 是否携带 __pycache__
deep invariant pass 是否成功
```

Doctor 不修复状态，只记录：

```text
audit_log
life_invariant_checks
trace_run
```

### 2.3 Invariant Check Table

Schema v11 新增：

```sql
CREATE TABLE life_invariant_checks (
  id TEXT PRIMARY KEY,
  owner_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  status TEXT NOT NULL,
  checks_json TEXT NOT NULL DEFAULT '{}',
  issues_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

它让每次健康检查本身也成为可审计记录。

### 2.4 包体卫生

v0.9 的 zip 中曾包含 `__pycache__` 和 `.pytest_cache`。v0.9.1 打包时显式排除：

```text
__pycache__/
*.pyc
.pytest_cache/
```

这减少安装噪音，也避免误把本地测试缓存当作插件源码。

---

## 3. 当前闭环判断

截至 v0.9.1：

```text
Canon 设定闭环：已实现
Pause / setup 防污染闭环：已实现
LifeOps mutation 闭环：已实现
CommitReceipt / FinalGate 闭环：已实现
Scalar Resource Ledger 闭环：已实现，并有 doctor/reconcile 检查
Entity Inventory / Meals 闭环：已实现
TruthSource resolve / observe / cache / trace 闭环：已实现
WakeJob Heartbeat 闭环：已实现
User Life Confirmation 闭环：已实现
Goals / Life Arcs / Event Decomposition 闭环：已实现
Reflection 复盘闭环：已实现
Autonomy Planner 决策与执行闭环：已实现
Proactive Intent / Outbox / State 闭环：已实现
Narrative Execution / Serendipity 闭环：已实现
Trace hash-chain verify：已实现
Doctor / invariant health check：v0.9.1 已实现
Lifecycle validator hardening：v0.9.1 已实现
```

---

## 4. v0.9.1 测试覆盖

完整测试集：

```text
31 passed
```

新增覆盖：

```text
1. doctor 对 fresh active agent 返回 ok/warning。
2. doctor 能检测 resource ledger drift。
3. completed event 不能被 transition 回 planned。
4. terminal event 不能再创建 active schedule block。
```

---

## 5. v0.9.2 建议

v0.9.2 建议继续做小版本，不扩新模块：

```text
1. trace explain 输出结构化 explanation tree。
2. explain event_id 时展示 receipt facts、truth reads、resource deltas、proactive intents。
3. explain wake_job_id 时展示 execution decision 和 resulting transaction。
4. explain proactive intent 时展示 score、policy、outbox、delivery state。
```

这样 v1.0 前可以让 trace 从“能查”升级成“能解释给产品/开发者看”。
