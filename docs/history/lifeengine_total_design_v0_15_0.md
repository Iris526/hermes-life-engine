# LifeEngine v0.15.0 — 资源模型整形 (Resource Model Reshape)

## 缘起

v0.14.0 让生命循环按真实时间转起来之后,用户审视了"身体资源"这一层,发现
四条 vital 轴的设计参差不齐:

- **精力 (energy)** — 真正驱动行为(恢复、硬扛日程),核心轴。
- **疲劳 (fatigue)** — 真正驱动行为(补觉/降级阈值),但与精力强反相关。
- **专注 (focus)** — **死轴**:被工作/睡眠扣减,却没有任何一处读它的值来改变行为
  (驱动降级的是从睡眠债算出的 `focus_penalty`,与 focus 资源无关)。
- **心情 (mood)** — **近乎死轴**:被三餐/小确幸写入,但除"心情低→更想吃安慰食物"外
  几乎不反哺决策,agent 也没有主动调节它的手段。

同时,事件的精力扣减是节律模板里**写死的预设**,agent 自己创建的事件不传成本时
默认 `{}`(白干不掉血)。

本轮目标:把资源模型整形成"每条轴都名副其实",并把成本判断交还给 agent。
用户拍板:**删除 focus;保留 fatigue;心情做成 agent 可触发并反哺行为;精力成本由 agent 判断。**

版本:0.14.0 → **0.15.0**。schema 不变(51,无新表 —— 心情反应走资源账本 + journal)。

**铁律**:任何资源变动仍走 `apply_delta()`,doctor 对账不变式
(`resource_accounts.current_value == SUM(resource_ledger.delta)`)始终成立。

---

## 1. 删除 focus(专注)

focus 不再是默认 vital 资源。

- `constants.py` / `living.py`:从默认 Canon 模板与归明观资源集中移除 focus。
- 节律模板里原先的 focus 成本折算进 energy(例如净符委托 `energy:-12, focus:-10` →
  `energy:-18`),保持"用力的事仍然掉血"。
- `sleep_effects.py`:不再把睡眠不足的 focus 惩罚写进 focus 账本;但**保留**派生信号
  `focus_penalty`(进 `mind_state`),它仍驱动 autonomy/execution 的"降级"决策
  —— 它本就是个认知惩罚信号,不是资源。
- `autonomy.py`:移除目标步事件里的 focus 成本与 score 里的 focus 读取。
- `settings_check.py`:从建议资源集中移除 focus。
- WebUI:精力/心情/疲劳三条 vital 轴展示,去掉 focus 球与样式。
- `canon.py` 的 "专注"→focus 关键词映射**保留** —— 资源是可插拔的,宿主/用户若主动
  定义专注仍可解析;只是不再属于默认模型。
- 既有库里残留的 focus 账户变为惰性数据(仍对账,不再被读写),无需破坏性迁移。

## 2. 保留 fatigue(疲劳)

不动。疲劳是独立的"该睡了"轴 —— 可以精力尚可但疲劳高(熬夜、晃神),它继续驱动
补觉/降级阈值,与 energy(还能不能行动)互补。

## 3. 心情 (mood):agent 可触发 + 反哺行为

新增 `emotion.py`:

- `record_mood_reaction(delta, reason, trigger)`:agent 把"某件事让自己心情如何"
  落账。**每次有界**(±20),走 `apply_delta` + `mood_reaction` journal,可审计。
- `mood_band`(low/neutral/high)、`mood_bias`(柔性语气/活动倾向提示)、
  `current_mood`、`recent_mood_reactions`。

接线:

- 新 LifeOp `MOOD_REACTION`(validators / runtime `_apply_op` / receipts)。
- 新工具 `life_mood`(`react` / `status`):agent 主动触发情绪、读自己的心情与近期反应。
- **反哺行为**:心情进入每轮上下文胶囊(band + 行为提示;工作平台只给 band 不泄私域),
  进 `life_status`;并继续喂养 persona `optimism` 漂移与三餐安慰倾向。心情不再是死表盘。

## 4. 精力成本交给 agent 判断

新增 `event_costs.py`:

- `estimate_event_cost(event_type, duration)`:按"活动类型 × 时长"给出温和的基线成本
  (energy 掉血 + 部分活动的 mood 增益),**只对已识别的活动类型**估算;未知/标记类
  事件不臆测、保持免费。
- `create_event`:仅当调用方**没给** `resource_costs`(None)时回填基线;agent 显式给的
  估值、或显式 `{}`(确实免费)永远优先。回填只针对本世界**已定义**的资源,绝不在未定义
  资源上凭空造成本。
- `life_event` 指引改为要求 agent 自己判断每个事件的成本;新增 `action=estimate_cost`
  作为参考基线。

---

## 验证

- 新增 `tests/test_lifeengine_v015_mood.py`(心情有界/落账/反哺/对账)、
  `tests/test_lifeengine_v015_event_costs.py`(基线估算/缺省回填/显式优先/未定义资源不臆造)。
- 受影响的睡眠/执行/webui 测试同步去 focus;睡眠债"降级/顺延"路径(focus_penalty 信号)
  保持绿。
- 全量 fast 套件通过;doctor 资源对账保持绿。

## 风险登记

- 🟡 删除 focus 触及面广(10 源文件 + 若干测试)。已逐一改为去 focus,睡眠降级信号保留。
- 🟢 心情/成本为加性能力,既有行为不破坏;成本回填对未定义资源与显式 `{}` 都安全。
- 🟢 schema 不变,无迁移风险;残留 focus 账户惰性存在、仍对账。
