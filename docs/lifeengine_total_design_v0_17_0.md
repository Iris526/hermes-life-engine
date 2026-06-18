# LifeEngine v0.17.0 — 经营系统 (Venture System)

## 缘起

用户让明灯"摆摊赚钱",并提出经营要从**引擎层面**保证一整套真实约束:进销存(货不能凭空
出现)、经营占时间(除非自助/雇人)、委托要能**自己找上门**(不是问了才现编)、营生**互不
冲突**(一人不能分身)、可注册多个、各自收支/触发/绑定规则、经营**位置**(固定/灵活)。

v0.16.0 已落地"周期活动(营生)"作为调度触发的地基。本轮把它扩成完整的 **Venture(经营体)**,
分四阶段实现,全部由心跳保证,不靠提示词/记忆。引擎不硬编码任何具体营生——摆摊只是注册数据,
**角色无关**。

版本:0.16.0 → **0.17.0**;schema 52 → **56**(P1=53, P2=54, P3=55, P4=56)。
铁律:所有收支/库存变动走 `apply_delta()`,doctor 对账不变式不破。

## Venture 数据模型(recurring_activities 扩展)

- **触发** `trigger_kind`:`scheduled`(按节奏铺)/ `opportunity`(委托找上门)/ `manual`。
- **经营模式** `operation_model`:`active`(亲自,占时+耗力)/ `self_service`(自助被动)/ `staffed`(雇员)。
- **位置** `location_kind`(fixed/flexible)+ `location`。
- **进销存** `supply_chain_json`:{goods_resource, unit_price, demand_per_occurrence, money_resource, restock:{threshold,quantity,unit_cost}}。
- **到达** `arrival_json`:{per_day, duration_minutes}(opportunity 用)。
- **工资** `wage_per_occurrence`(staffed 用)。

## P1 — 经营体地基 (schema v53)
- 加 operation_model / trigger_kind / location_kind / location 字段。
- **不分身**:materialize 营生事件时走 `impromptu._next_free_slot` 冲突裁决,窗口被占则顺延到空档,绝不双占。

## P2 — 进销存 (schema v54)
- 货品/材料建模为**资源**(`stock.*`,走审计账本,像钱一样);注册供应链营生时自动定义货品资源。
- **卖货**(经营事件完成时):卖出 `min(需求, 库存)` → 扣库存 + 进账(卖出×单价);缺货当天无收入。供应链营生事件剥掉静态 money,收入只从销售来。
- **进货**:库存 < 阈值 → 心跳自动铺一个进货事件(成本=补货量×进货价,完成时入库),去重(`venture_restock_orders`),没钱按缺钱顺延。
- 制作配方(材料→成品)留 P2b。

## P3 — 触发机制 (schema v55)
- opportunity 营生不按节奏铺;心跳按 `arrival.per_day` **确定性掷点**(hash(venture,日期),可复现、可测,无 RNG)算出当天到达数。
- 把"还没到的"委托落成事件(从 now 起进空档,多个串行不撞车),事件带报酬(resource_costs),完成时入账。`venture_opportunity_arrivals` 控当天上限 + 跨 tick 去重。

## P4 — 经营模式行为 (schema v56)
- `active`:占时(冲突裁决的排期块)+ 耗力;销售在事件完成时结算。
- `self_service`/`staffed`:**不占她的时间**(不建排期块、不耗体力);在**窗口结束**时被动结算(无需她在场);无窗口则当 tick 立即结算。
- `staffed`:每次结算扣 `wage_per_occurrence`(雇员工资)。
- 位置 fixed/flexible 落在事件上(opportunity 外勤为 flexible)。

## 工具 & 接线
- 工具 `life_activity`:register/list/get/pause/resume/cancel/update,支持上面全部字段。
- 心跳新增子流程:`_materialize_recurring_for_tick`(调度铺事件+不分身)、`_settle_supply_chain_for_tick`(销售/被动结算/工资/补货)、`_roll_opportunities_for_tick`(委托到达)。
- 模块门控 `recurring_activities`(默认 auto)统管整套。

## 验证
- `test_lifeengine_v016_recurring_activities` + `..._venture_p1/p2/p3/p4`:幂等/节奏/取消、进销存闭环、委托到达、经营模式行为(无块/被动结算/工资/active 占时)。
- 全量 fast 套件通过;doctor 资源对账保持绿。

## 待办
- **P2b** 制作配方(材料库存→成品库存的制作事件)。
- 自然到达的随机性可在 per_day 之外再加波动;flexible 位置可接入地点库。
