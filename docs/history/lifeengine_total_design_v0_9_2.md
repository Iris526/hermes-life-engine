# LifeEngine 总设计文档 v0.9.2

> **v0.9.2 主题：Install / Upgrade / Migration / Command-Path Hardening。**
>
> v0.9.1 已经把生命周期、doctor、自检和状态机硬约束补上。v0.9.2 不新增生活行为能力，而是强化安装、升级、迁移、备份、索引重建、heartbeat cron 脚本测试、CLI/slash 命令链路，为 v1.0 做可发布性收敛。

---

## 1. 版本定位

v0.9.2 是 v1.0 前的小版本迭代，不改变 LifeEngine 的核心理念：

```text
Life Canon First
State Before Narrative
Every durable mutation goes through LifeOps
Every LifeOps transaction produces Journal + CommitReceipt + Trace
Paused/setup states never mutate life state
User Life requires confirmation / trusted source
```

v0.9.2 只做工程硬化：

```text
1. fresh install 可诊断
2. old DB upgrade 可诊断
3. migration ledger 可查看
4. DB backup 可执行、可审计
5. FTS/sqlite-vec memory index 可重建
6. heartbeat cron 脚本可安装、可检查、可单次测试
7. CLI / slash command 关键路径有 smoke test
8. doctor 能覆盖 install/upgrade 相关 surface
```

---

## 2. v0.9.2 新增 Schema

Schema version 提升到 12。

新增/强化表：

```text
schema_migrations
upgrade_runs
db_backups
maintenance_runs
cron_heartbeat_tests
install_checks
```

### 2.1 schema_migrations

记录 schema 版本迁移和 backfilled legacy migration。

用途：

```text
/life trace migrations
hermes lifeengine trace migrations
hermes lifeengine upgrade check --include-details
```

### 2.2 upgrade_runs

每次 install/upgrade check 都会记录：

```text
plugin_version
db_user_version
expected_schema_version
checks_json
status
```

### 2.3 db_backups

`life_upgrade backup` 使用 SQLite online backup API 创建一致性 DB 备份，并记录：

```text
backup_path
size_bytes
reason
status
```

### 2.4 maintenance_runs

记录索引重建等维护动作。

### 2.5 cron_heartbeat_tests

记录 heartbeat script 单次运行结果：

```text
script_path
returncode
stdout
stderr
status
```

---

## 3. 新增工具：life_upgrade

`life_upgrade` 是 v0.9.2 的维护工具，不创建生活事件，不改变 Agent 的叙事生活。

支持：

```text
check / status
backup / backup_db
backups / list_backups
rebuild_memory / rebuild_indexes / rebuild
maintenance / maintenance_runs
cron_test / heartbeat_test / test_tick_script
```

示例：

```json
life_upgrade({"action":"check", "include_details":true})
life_upgrade({"action":"backup", "reason":"before migration test"})
life_upgrade({"action":"rebuild_memory"})
life_upgrade({"action":"cron_test", "script_path":"~/.hermes/scripts/lifeengine_tick.py"})
```

---

## 4. CLI / Slash 命令

### 4.1 CLI

```bash
hermes lifeengine upgrade check --include-details
hermes lifeengine upgrade backup --reason "before testing"
hermes lifeengine upgrade backups
hermes lifeengine upgrade rebuild_memory
hermes lifeengine upgrade maintenance
hermes lifeengine heartbeat status
hermes lifeengine heartbeat test
hermes lifeengine heartbeat install --schedule "every 5m" --deliver local
```

### 4.2 Slash

```text
/life upgrade
/life upgrade backup
/life upgrade rebuild
/life upgrade cron_test
/life heartbeat status
/life heartbeat test
```

这些命令全部走本地 SQLite 和本地脚本，不调用 LLM。

---

## 5. Heartbeat Cron Hardening

v0.9.2 保持 Hermes no-agent cron 设计：

```text
Hermes scheduler -> no-agent script -> LifeEngine tick -> stdout if abnormal
```

生成脚本：

```text
~/.hermes/scripts/lifeengine_tick.py
```

健康 tick 应该 stdout 为空，保持 Hermes script-only cron 的 silent tick 模型。

新增能力：

```text
heartbeat status       检查脚本是否存在、是否最新、是否可执行、Hermes 是否在 PATH
heartbeat test         用当前 Python 运行脚本一次，并把结果写入 cron_heartbeat_tests
heartbeat install      写脚本并返回 hermes cron create 命令
heartbeat install --run 写脚本并尝试创建 no-agent cron job
```

---

## 6. DB Backup 与 Memory Index Rebuild

### 6.1 DB Backup

`life_upgrade backup` 用 `sqlite3.Connection.backup()` 生成一致性备份。

默认路径：

```text
$HERMES_HOME/lifeengine/exports/backups/lifeengine-<timestamp>.db
```

### 6.2 Memory Rebuild

`life_upgrade rebuild_memory` 会重建：

```text
memory_fts
memory_vec
```

它不会改变 memory 内容，只重建索引。

---

## 7. Doctor 集成

Doctor 现在覆盖 install/upgrade surfaces：

```text
schema_migrations 是否存在
install_checks 是否存在
heartbeat script 是否存在且 current
schema version 是否等于当前 _SCHEMA_VERSION
sqlite-vec 是否可用
required tables 是否完整
journal hash chain 是否有效
resource ledger 是否一致
wake jobs 是否 stuck
package hygiene 是否健康
```

Doctor 仍然只诊断，不自动修复。

---

## 8. 当前闭环状态

截至 v0.9.2：

```text
Canon 设定闭环：已实现
Pause / setup 防污染闭环：已实现
LifeOps mutation 闭环：已实现
CommitReceipt / FinalGate 闭环：已实现
Scalar Resource Ledger 闭环：已实现
Entity Inventory / Meals 闭环：已实现
TruthSource resolve / observe / cache / trace 闭环：已实现
WakeJob Heartbeat 闭环：已实现
User Life Confirmation 闭环：已实现
Goals / Life Arcs / Event Decomposition 闭环：已实现
Reflection 复盘闭环：已实现
Autonomy Planner 决策与执行闭环：已实现
Proactive Intent / Outbox / State 闭环：已实现
Narrative Execution / Serendipity 闭环：已实现
Lifecycle / Doctor / Trace diagnostics：已实现
Install / Upgrade / Backup / Rebuild / Heartbeat script diagnostics：已实现
```

---

## 9. v1.0 前建议路线

```text
v0.9.3
  FinalGate repair UX
  better refusal/rewrite text
  optional Hermes mandatory final gate patch draft

v0.9.4
  export/import/restore
  branch archival and profile migration
  data compaction policies

v0.9.5
  performance and concurrency stress tests
  multi-session / multi-heartbeat contention
  large memory/resource/event dataset tests

v1.0-rc
  API/schema freeze
  examples and acceptance scenarios
```

v0.9.2 的目标是把“能安装、能升级、能诊断、能备份、能测试 heartbeat 脚本”做稳。
