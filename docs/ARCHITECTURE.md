# LifeEngine 现状总设计（常青）

一份**描述现状**的架构文档（不是某个版本的 delta）。目标：新读者（包括每晚自迭代的 agent 自己）3 分钟内能画出「它如何活着」。术语以 [GLOSSARY.md](GLOSSARY.md) 为准；50+ 份按版本号命名的历史「总设计」是 delta，存于 `docs/history/`（本文取代它们作为入口）。

> 维护约定：**改架构先改这里**。文档随代码走，别让它变成又一张停在旧 schema 的图。

---

## 0. 一句话

LifeEngine 是一个**代码强制执行的生活运行时**：一个 agent 拥有自己的生活（资源、日程、睡眠、营生、社交、记忆、观点），由确定性底座保证账目可审计，由生成层（LifeAuthor）让内容像活人。它作为 Hermes 插件运行，也能在无 agent 的 cron 心跳里独立推进——**用户不在时她也在过日子**。

---

## 1. 两层生命架构（核心设计哲学）

```
┌─────────────────────────────────────────────────────────────┐
│  生成层（LifeAuthor）—— 只写"内容"，绝不碰资源                    │
│  梦 / companion 主动语 / 日程叙事 / reflection→观点 / campaign  │
│  宿主模型不可用 → 返回 None → 降级到确定性模板（dev/CI/离线安全）  │
├─────────────────────────────────────────────────────────────┤
│  确定性底座 —— 一切持久变更走 LifeOps                            │
│  资源账本 / Event·ScheduleBlock 状态机 / 睡眠 / 营生结算 /       │
│  心跳 tick / module_gates / 收据·journal 哈希链                 │
└─────────────────────────────────────────────────────────────┘
```

**铁律（用户强制）：**
1. **一切持久变更走 LifeOps**：validate → savepoint → receipt → journal 哈希链。资源变更只经 `apply_delta()`，doctor 不变量 `resource_accounts.current_value == SUM(resource_ledger.delta)` 恒成立。
2. **角色无关**：引擎不硬编码角色/世界观，内容只从 **Canon**（真源）生成。明灯只是默认皮肤。（现状：6 个域仍有角色硬编码，轴三待清。）
3. **降级即安全**：`life_author.py` 是唯一的模型调用缝；宿主 `PluginLlm` 不可用时返回 None，全部回落模板。离线/CI/dev 行为与生产一致，doctor 保持绿。

`life_author.author()` 是全系统最干净的抽象：best-effort、按 kind 预算、绝不抛异常、绝不改资源、网络发生在写事务之外。

---

## 2. 心跳 tick（后台推进）

`LifeEngineRuntime.tick()`（runtime.py）由**模块注册表** `_HEARTBEAT_MODULES = [(out_key, method_name, extra_ctx_keys)]` 驱动——`tick()` 是一个 for 循环，新增心跳模块 = 往注册表加一行（轴二-1 消灭了此前的「4 处平行清单」漂移）。

当前心跳模块（注册表顺序即执行顺序）：`autonomy`、`persona_drift`、`reflection`、`meals`、`venture`（营生物化）、`campaigns`、`daily_rhythm`、`venture_supply`（进销存结算）、`venture_opportunities`（机会到达）、`realtime_sync`、`companion`、`proactive`、`managed_review`。

非模块化的 tick 收尾仍内联：resource_recovery、wake_jobs、schedule_sweep、truth_refresh、delayed_reply_release。

心跳按 **real elapsed wall-clock** 结算资源（`TICK_BASELINE_MIN` 基准 + `GAP_CAP_MIN` 封顶），离线补算不失真。无 agent 的 `heartbeat.py` cron 直接 `LifeEngineRuntime().tick()`，让 LifeAuthor 在后台生活。

---

## 3. 模块门控（module_gates）

每个心跳/行为模块有一个档位，**单一真值源 = `constants.GATE_SPECS`**（轴二-2）：

```python
GATE_SPECS[key] = GateSpec(kind, default, consumer, allowed[, numeric_range])
```

- `kind="mode"` 枚举门（`allowed` 词表）；`kind="numeric"` 数值配置（如 `context_budget_chars`，范围对齐 context_policy）。
- `consumer` 写明真实运行期读取点——**无 consumer 的门不允许存在**（`test_lifeengine_gate_schema` 钉死）。
- `DEFAULT_MODULE_GATES` 从注册表**派生**，无法手抄漂移。37→20 键（删了 18 个零读取死键）。
- 写入经 `canon.set_module_gate` → `validate_gate_value`：未知键/越界值直接 raise（此前是零校验的「杂物抽屉」）。

---

## 4. 五个概念簇与数据模型

详见 GLOSSARY；架构层面：

1. **确定性生活底座**：LifeOps/收据/journal 链、资源账本、Event/ScheduleBlock、venture（营生）三套物化、睡眠、realtime state、serendipity。
2. **生成式内心生活**：LifeAuthor seam、梦、companion、proactive intent→outbox→delivery、campaign（资料片）、reflection→opinions、persona 六维、mood、memory。
3. **世界与社会**：Canon（真源）、world_model（本体/地图/地点/传说/势力）、social_world（实体/关系/声望/评价/流言）、social_projector。
4. **治理与人审**：ReplyGate、FinalGate+receipts、review 收件箱（"她想跟你说的事"）+ managed_review_loop、doctor/invariants、时间仲裁、confirmations。
5. **表面层**：WebUI（观察站）、`TOOL_REGISTRY` 的 40+ 个 life_* 工具、CLI/slash 五面、plugin.yaml。

**Schema**：`db.py` 顺序迁移 `_create_schema_vN`（当前 v70），驱动按 `PRAGMA user_version` 增量应用；新库顺序重放，存量库从各自版本追平。表改名/删表走**前向迁移**（如 v68 删仪式表、v69 venture、v70 nightly_check），不做手写 baseline 重建（易漂移，收益已被测试基建覆盖）。`db.reference_table_names()` 迁移一个内存参照库来派生「健康库应有的表集」，doctor 据此检查（消灭了此前停在 v39 的手抄清单）。

---

## 5. 写路径：LifeOps

```
commit_ops(ops) → 单事务 BEGIN IMMEDIATE
  每个 op：validators.validate → savepoint → 执行 → receipts.build → append_journal（哈希链）
  失败：回滚该 savepoint，记 failed_lifeops_audit，外层继续
```

- **op 类型**在 `validators.VALID_OP_TYPES`；分发在 `runtime._commit_ops_locked`。改名时旧 op 类型保留为向后兼容别名（如 `CREATE_RECURRING_ACTIVITY` → `CREATE_VENTURE`）。
- **收据**（commit_receipts + facts）是人可读的"发生了什么"；**journal** 是防篡改哈希链（doctor 验链）。
- 资源效果一律 `apply_delta()`。

---

## 6. 命令面（单一注册表）

`__init__.TOOL_REGISTRY` 是工具面单一真值源：`(name, schema, handler, description, emoji)`。`register(ctx)` 遍历它注册。`test_lifeengine_command_surface` 钉住 `TOOL_REGISTRY ↔ plugin.yaml` 不漂移、schema.name == 注册名（别名对如 life_venture/life_activity 不得分叉）。

五面：argparse（`cli.py setup_cli_parser`）/ slash（`slash_life`）/ tools（`TOOL_REGISTRY`）/ schemas（`schemas.py`）/ plugin.yaml + SKILL.md。人类命令面刻意小（`/life help|status|setup|commit|pause|resume|run|review|doctor|...`），完整工具面给 agent。

Hooks：`pre_gateway_dispatch` / `pre_llm_call` / `post_tool_call` / `transform_llm_output` / `on_session_start` / `on_session_end`。

---

## 7. 治理与健康

- **ReplyGate**（reply_gate.py）：睡眠/不可打断时延迟回复；`life_call` 可紧急唤醒放行。
- **FinalGate**（final_gate.py）：最终输出审计，默认 advisory。
- **review 收件箱**（review.py）：把待处理项聚合成"她想跟你说的事"；`managed_review_loop` 心跳自动安全应用（真实循环 `_run_agent_managed_review_locked`）。合成的 acceptance/stress/observability 元塔已删（轴二-3）。
- **doctor**（doctor.py + invariants.py + `runtime.doctor()`）：表存在性（从 schema 派生）、资源对账、事件生命周期、wake_job、journal 哈希链。

---

## 8. 扩展点（都是"可注册的数据"，不是硬编码）

| 要加… | 改这里 |
|---|---|
| 一个心跳模块 | `runtime._HEARTBEAT_MODULES` 加一行 + 写 `_run_x_for_tick` |
| 一个门控 | `constants.GATE_SPECS` 加一条（带 consumer） |
| 一个工具 | `__init__.TOOL_REGISTRY` + `schemas.py` + `tools.py` + `plugin.yaml` |
| 一个 LifeOp | `validators.VALID_OP_TYPES` + 分发 + `receipts` |
| 一张表 | `db.py` 新 `_create_schema_vN` + bump `_SCHEMA_VERSION` |
| 一种资源/机制 | agent/host 通过 Canon + LifeOps 注册（不改引擎代码） |

---

## 9. 已知在建（现状诚实标注）

- **角色外置**（轴三）：师兄/Ringo/归明观/明灯/符纸词表仍散在 6 个域，待迁到 Canon。
- **生成层接管机械路径**（轴三）：执行收尾/serendipity/auto_send outbox 仍是模板，待接 LifeAuthor。
- **单 agent 假设**（轴五）：owner 已参数化，但 social_world 无法表达第二个真 agent；user_id 身份分裂待统一。
- **WebUI**（轴四）：数据库后台感待改造成生命观察站。

审计与五轴改造计划见 [audit_2026_07_02_refactor_plan.md](audit_2026_07_02_refactor_plan.md)。
