# LifeEngine v0.16.0 — 营生 / 周期活动 (Recurring Activities)

## 缘起

用户跟 agent 商量让她"摆摊赚钱",问:这种**反复的营生**能不能从**引擎层面**注册/取消
——由引擎保证地反复产出事件、结算收入,而不是靠提示词或记忆让她"记得去做"。

审计现状:引擎**没有**周期/重复事件原语(`recurrence`/`rrule` 零命中);每日节律
(`living.rhythm_templates`)是**硬编码 + 手动触发**,不可运行时注册;goal/life_arc 是
长期锚点但不自动产出周期收入事件。所以"摆摊营生"此前**无法**在引擎层面注册/取消。

本轮新增一个**通用、角色无关**的「营生 / 周期活动」原语:agent 注册一次,心跳每天
自动把到期的营生铺成一个具体排期事件,收入/成本走正常事件完成路径结算,可暂停/取消。
引擎不硬编码任何具体营生——摆摊只是 agent 注册进来的一行数据。

版本:0.15.0 → **0.16.0**;schema 51 → **52**。

**铁律**:收入/成本仍走 `apply_delta()`(事件完成时),doctor 对账不变式不破。

---

## 数据 (schema v52)

- `recurring_activities`:id、owner、title、`activity_type`(materialized 事件的 event_type)、
  event_category、activity_domain、`cadence_kind`(`daily`|`weekly`)、`weekdays_json`(weekly: 0..6, Mon=0)、
  `start_time`/`end_time`(HH:MM local)、`timezone`、`resource_costs_json`(每次的签名增量,含收入,**agent 判定**)、
  importance/priority、`status`(active|paused|cancelled)、start_date/end_date、last_materialized_date、tags、source。
- `recurring_activity_occurrences`:`UNIQUE(activity_id, date_key)` —— materialize 幂等的依据 + 审计
  (记录每天由哪个营生产出哪个 event/schedule_block)。

## 模块 `recurring.py`

- `create_recurring_activity` / `update_recurring_activity`(改状态/字段)/ `list` / `get`。
- `_matches_cadence(activity, date_key, weekday)`:daily 恒真;weekly 看 weekday ∈ weekdays;含 start/end_date 边界。
- `due_activities(...)`:active + 到期 + 当天**尚未** materialize(查 occurrences)→ 待铺列表。
- `record_occurrence(...)`:写 occurrence(`INSERT OR IGNORE`)+ 更新 last_materialized_date。

## LifeOp 接线

- `CREATE_RECURRING_ACTIVITY` / `UPDATE_RECURRING_ACTIVITY`(validators / runtime `_apply_op` / receipts)。
- materialize 出来的事件复用现成的 `CREATE_EVENT` + `CREATE_SCHEDULE_BLOCK`。

## 引擎保证:心跳 materialize

- 新 per-tick 子流程 `_materialize_recurring_for_tick`(gated `recurring_activities`,默认 auto):
  按 agent 本地日期/星期取 `due_activities`,对每个**当天未铺**的营生:
  建 `CREATE_EVENT`(带营生的 resource_costs,即 agent 判定的收入/成本)+ 当天时间窗的
  `CREATE_SCHEDULE_BLOCK`,然后 `record_occurrence`(幂等)。
- 收入/成本在该事件被执行模拟器完成时,通过 `complete_event → apply_delta` 入账(含正向 `money.*`)。
- tick 返回带 `recurring_activities` 字段;事件进 journal/receipt/trace,全程可审计。

## 工具 `life_activity`

`register` / `list` / `get` / `pause` / `resume` / `cancel` / `update`。注册示例:
摆摊 = `{title:"在东市摆摊", cadence_kind:"daily", start_time:"10:00", end_time:"14:00",
resource_costs:{"money.lingzhu":30, "energy":-14}}`。**取消** = status→cancelled,不再 materialize
(历史 occurrence 保留)。

---

## 验证

新增 `tests/test_lifeengine_v016_recurring_activities.py`:
- schema/version pin(52 / 0.16.0)。
- 每日营生:一次 tick 铺一个事件、当天再 tick **不重复**(幂等)、隔天**新铺**一个;事件带 agent 判定的收入成本。
- 取消:状态置 cancelled 后,tick **不再** materialize。
- 周节奏:非匹配星期不铺;改成当天星期后当天铺。
全量 fast 套件通过;doctor 资源对账保持绿。

## 风险登记

- 🟡 schema v52 幂等(`IF NOT EXISTS` / `INSERT OR IGNORE` / `UNIQUE`);旧库增量升级。
- 🟢 加性能力:不动既有事件/节律路径;materialize 仅在 gate=auto 且有 active 营生时产出。
- 🟢 角色无关:引擎不含任何具体营生;摆摊是注册数据,不绑明灯/宿主。
