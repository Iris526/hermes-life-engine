# LifeEngine v0.12.1 — WebUI Live Observatory 增强设计

## 目标

v0.12.1 继续强化 v0.12.0 的 WebUI / Observatory，不改变 LifeEngine 核心数据库 schema。重点是把 WebUI 从静态轮询面板升级为更像“运行时观察台”的界面：实时更新、事件详情、梦境详情、Trace explain、Owner 切换、可点击的 schedule timeline，以及更清晰的小人状态动效。

## 设计原则

1. **WebUI 默认只读观察**：任意选择的外部 LifeEngine DB 都只读；只有当前 Hermes profile 的 DB 才允许有限操作。
2. **不绕过 LifeEngine Runtime**：Call、Tick、Review apply、补觉计划等动作仍通过 Runtime，不直接改表。
3. **人类友好优先**：页面展示 timeline、item、detail drawer，不展示原始 JSON 作为默认视图；JSON 只在详情中作为调试附加信息。
4. **实时但保守**：使用 Server-Sent Events 推送 snapshot；如果浏览器不支持 SSE，可以手动刷新。
5. **小人是状态投影，不是另一个状态源**：像素小人的状态来自 `agent_realtime_state`、active event、sleep day state、delayed replies、review items。

## 新增 WebUI 能力

### 1. SSE 实时流

新增前端 EventSource 连接：

```text
GET /api/stream?period=today|tomorrow|week|day&date=YYYY-MM-DD
```

后端每 2 秒读取 snapshot，hash 变化时推送 `snapshot` 事件，否则推送 `heartbeat`。

### 2. Event Detail Drawer

新增：

```text
GET /api/event/{event_id}
```

返回：

```text
event
state transitions
schedule blocks
schedule transitions
actions
action transitions
results
resource ledger
memories
dream references
proactive intents
execution sleep adjustments
journal references
```

前端点击 schedule item、recent event、current event 后打开右侧详情抽屉。

### 3. Dream Detail Drawer

新增：

```text
GET /api/dream/{dream_id}
```

返回 dream entry、dream runs、audit findings、journal references。梦境仍保持：

```text
truth_layer = dream_symbolic
```

### 4. Trace Explain Drawer

新增：

```text
GET /api/trace/explain/{object_id}
```

支持解释：

```text
event_id
dream_id
transaction_id
journal_id
任意被 journal payload 引用的 id
```

### 5. Owner 切换

顶部新增 owner selector。WebUI 会扫描：

```text
engine_control
events
schedule_blocks
agent_realtime_state
resource_accounts
memories
```

中的 distinct owner，并允许切换观察对象。

### 6. 状态动效增强

小人状态继续由 Avatar State Mapper 映射：

```text
asleep / napping       → sleep
mode=dreaming          → dream
waiting_to_reply       → reply
uninterruptible_event  → battle
work/study/creative    → work
travel/health/fitness  → walk
meal                   → eat
sleep debt / fatigue   → tired
else                   → idle
```

v0.12.1 在 CSS 上增加了 live indicator、状态符文、detail drawer、hover 可点击状态和更多状态动效。

## 后端接口

```text
GET  /api/health
POST /api/select
GET  /api/meta
GET  /api/owners
POST /api/owner
GET  /api/snapshot
GET  /api/state
GET  /api/schedule
GET  /api/events
GET  /api/event/{event_id}
GET  /api/dream/{dream_id}
GET  /api/trace/explain/{object_id}
GET  /api/review
GET  /api/resources
GET  /api/dreams
GET  /api/trace/latest
POST /api/action
GET  /api/stream
```

## 版本信息

```text
Plugin version: 0.12.1
DB schema version: 39
WebUI: FastAPI + static frontend + SSE
```

## 下一步建议

v0.12.2 可继续增强：

```text
1. 独立 Event Graph 页面
2. Trace graph 可视化
3. 多 Agent dashboard
4. 像素小人多帧 sprite sheet
5. WebUI 操作审计页面
6. 可自定义 avatar sprite 包
```
