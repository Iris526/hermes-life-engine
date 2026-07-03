# LifeEngine 术语表（GLOSSARY）

单一术语真值源。52 个版本的「只叠不删」让同一个东西长出多个名字、一个名字指多个东西（审计 2026-07-02 §2）。这份表是**规范名**的落地载体：新读者（含 nightly agent 自己）从这里就能对齐概念，重命名（轴二-4/5）以这里为准。

状态标记：**✅ 已统一**（代码/DB 已一致）｜ **🟡 名义已定，代码待改**（规范名已定，标识符/持久化串/表名仍是旧名，随轴二-5 schema squash 原子改名）｜ **🔒 决策锁定**。

---

## 1. 确定性生活底座

| 规范名 | 含义 | 别名/旧名 | 状态 |
|---|---|---|---|
| **LifeOps** | 唯一的确定性写通道：validate→savepoint→receipt→journal 哈希链。一切持久变更必须走它。 | — | ✅ |
| **resource ledger** | 资源账本，恒等式 `resource_accounts.current_value == SUM(resource_ledger.delta)`；只经 `apply_delta()` 变更。 | — | ✅ |
| **Event / ScheduleBlock** | 事件与其排期块状态机。 | — | ✅ |
| **heartbeat tick** | 每 5 分钟（或离线补算）的后台推进；由 `_HEARTBEAT_MODULES` 注册表驱动（轴二-1）。 | — | ✅ |
| **module gate** | 每个心跳/行为模块的开关档位，单一真值源 `constants.GATE_SPECS`（轴二-2），写入经 `validate_gate_value`。 | 旧称杂物抽屉里的 37 键；已收敛为 20 键。 | ✅ |
| **venture（营生 / 经营体）** | 引擎级可注册/取消的**营生**：按 cadence 物化成日程事件的 occupation（摆摊赚钱等），含进销存/机会到达/经营模式。 | `recurring_activities`（表）、`life_activity`（工具）、occupation、经营体 —— **一物多名**。 | ✅ 全量改名完成（轴二-4/5，migration v69）：表 `ventures`/`venture_occurrences`、op `CREATE/UPDATE_VENTURE`（旧名向后兼容接受）、gate `venture`（旧 `recurring_activities` 回退读）、工具 `life_venture`（`life_activity` 别名）、模块 `venture.py`。保留为内部实现：私有方法 `_materialize_recurring_for_tick` 等、事件属性 `recurring_activity_id`（历史事件读取兼容）。 |
| **daily_rhythm** | 每日节律模板物化（晨巡等）。 | `living_rhythm`（legacy 读别名）。 | ✅（gate 已首类化，轴二-2） |
| **serendipity** | 偶遇/小意外的确定性映射（当前硬编码，待生成层接管=轴三）。 | — | ✅ |

## 2. 生成式内心生活（LifeAuthor）

| 规范名 | 含义 | 别名/旧名 | 状态 |
|---|---|---|---|
| **LifeAuthor** | 唯一的模型调用缝（`life_author.py`）：只写内容、绝不碰资源；宿主模型不可用→降级模板。 | — | ✅ |
| **dream（梦）** | 从生活域记忆/事件生成的象征性梦境；`truth_layer=dream_symbolic`，绝不是现实事实。 | — | ✅ |
| **nightly_check（夜间自检）** | 引擎夜间**系统自检**：找过期日程块→missed、待发延迟回复→release，写 findings。是引擎家务，**不是**角色做梦，产品目标「梦里禁止出现自检」。 | `DreamAudit`、`run_dream_audit`、`dream_audit_finding`、`audit_status`、`dream_audit_findings`（表）。 | 🔒 规范名=nightly_check，迁出梦域；🟡 函数/action/item_type/journal 事件/列名/表名待轴二-5 原子改（含向后兼容读旧值）。 |
| **companion** | 安静一段时间 + 好心情/该回访时，主动说一句（idle_share / ask_about_user）。 | — | ✅ |
| **campaign（资料片）** | 跨周主题长弧（预兆→升温→高潮→收尾），心跳逐日物化themed事件。 | 「资料片」曾也指 world_chronicle 的 `expansion_key`（实为 campaign-expansion 版本键，合法同义，非误用）。 | ✅（审计过度标记，NO-OP） |
| **reflection → opinion** | 每日回看形成/强化观点 + 一句自述（`memory_type='self_narrative'`）。 | goals.py 的 `life_reflections`（proposed_ops 从不应用）是**另一个** reflection，已决定不接线（见 [[hermes-lifeengine-recent-work]]）。 | ✅（v0.18 观点循环）｜ life_reflections 命名冲突待厘清 |
| **persona / mood** | 六维人格漂移 + 情绪计。 | — | ✅ |

## 3. 世界与社会

| 规范名 | 含义 | 状态 |
|---|---|---|
| **Canon** | 角色/世界观真源；引擎不硬编码角色，内容只从 Canon 生成（铁律，轴三待补角色外置）。 | ✅ 契约 / ❌ 6 域仍硬编码角色（轴三修） |
| **world_model** | 结构化世界本体：档案/地图/地点/传说/势力。 | ✅ |
| **social_world** | 社会槽：实体/关系/声望/评价/流言。 | ✅ |
| **truth_layer** | 真值分层枚举（`dream_symbolic` / `rumor_unverified` / user_life 需确认）。当前三套同名词表未统一。 | 🟡 待统一枚举（轴二-2 truth 部分/轴三） |

## 4. 治理与人审

| 规范名 | 含义 | 别名/旧名 | 状态 |
|---|---|---|---|
| **ReplyGate** | 回复延迟/放行策略机。 | — | ✅ |
| **FinalGate** | 最终输出审计（默认 advisory）。 | — | ✅ |
| **review inbox（她想跟你说的事）** | 人类收件箱聚合。 | — | ✅ |
| **managed_review_loop** | 心跳自动跑的、策略门控的安全 review 动作应用（真实）。 | 其上的 acceptance/stress/observability/release_readiness **元塔已删**（轴二-3）。 | ✅ |
| **doctor / invariants** | 健康检查与不变量。 | — | ✅ |

## 5. 已删除（轴二-3，验收剧场）

`acceptance_suite`（写死全 passed 合成）、`concurrency_smoke`、`integration_check`、`surface_snapshot`、`api_freeze`、`release_readiness`、review managed-review 元塔、`sleep_dream_acceptance.py`（合成）—— 全部删除，真实验证归 pytest。对应仪式表的物理 DROP 随轴二-5。

保留的**真实**嵌入式验收 runner（跑真 tick）：`sleep_autonomy_execution_acceptance`、`sleep_reply_dream_conversation_acceptance`、`sleep_reply_dream_policy_acceptance`（是否转纯 pytest 待定）。

---

## 概念债（重命名以本表为准，轴二-4/5 执行）

- **长期意图 ×4**：`goals` / `life_arcs`（**保留**，被动记录，不合并——用户裁定）/ `campaigns` / `venture`。四者各有心跳物化与幂等 occurrence 表，暂不强并。
- **一名多物**：`reflection`（v0.18 观点循环 vs goals 的 life_reflections）；`review`（人类收件箱 vs managed 自动循环）。
- **态度 ×3**：`persona_traits` / `agent_opinions` / `social_evaluations` 并存，分工待明确（轴三反哺行为时收敛）。

> 维护：改名或加概念时**先改这里**，再改代码；轴二-6 会加一致性测试把关键枚举/命令面 pin 到本表。
