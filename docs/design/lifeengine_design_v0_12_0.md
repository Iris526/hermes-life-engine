# LifeEngine v0.12.0 — WebUI / Observatory

## 目标

v0.12.0 把 LifeEngine 从命令行和底层 trace 系统推进到可观察产品面：用户选择某个 Agent 的 LifeEngine 目录后，可以用浏览器实时观察 Agent 的生活状态、日程、资源、梦、Review、流水和当前事件。

核心理念：

- **底层仍由 LifeEngine Runtime 决定生活状态。** WebUI 不重写生活逻辑。
- **默认人类友好。** 页面展示时间线、状态卡、像素小人和自然语言 item，不展示原始 JSON。
- **目录可选择。** 用户可以输入 LifeEngine 目录或 `lifeengine.db` 文件路径。
- **观察优先，操作谨慎。** 任意目录默认只读；只有当前 Hermes profile 的 DB 才允许少量 operator action。
- **角色可视化。** 默认像素形象来自用户提供的 Agent 参考图。

## 新增组件

```text
lifeengine/webui/
  server.py       FastAPI server / REST / SSE
  reader.py       read-only SQLite bridge and avatar-state mapper
  static/
    index.html    dashboard shell
    styles.css    cyber / dream / pixel styling
    app.js        frontend state/render loop
    assets/
      default-agent-reference.jpg
      default-agent-pixel.png
      default-agent-icon-pixel.png
```

## WebUI 页面

### Live Overview

展示：

- 像素 Agent 小人
- 当前模式：idle / asleep / dreaming / busy / waiting_to_reply
- 当前 event
- energy / fatigue / focus / mood
- sleep debt / recovery pressure / delayed replies / dreams

### Schedule Timeline

支持：

```text
today
tomorrow
week
指定日期
```

展示：

- 计划时间
- 实际时间
- block status
- event status
- event category / type
- location
- interruptibility

### Review Feed

展示人类可读 item，不展示内部 JSON。

### Resources / Dreams / Trace

展示资源卡、梦境卡、最近 life_journal 流水。

## Runtime Bridge

### REST

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
GET  /api/review
GET  /api/resources
GET  /api/dreams
GET  /api/trace/latest
POST /api/action
```

### SSE

```text
GET /api/stream
```

当前前端使用 polling 作为主路径，SSE 已提供给后续增强。

### Operator Actions

`POST /api/action` 支持：

```text
tick
call
review_apply
review_apply_all
sleep_recovery_plan
```

安全规则：

- 如果用户选择的是任意外部 DB：只读。
- 如果选择的是当前 Hermes profile 的 DB：允许上述有限 operator action。
- 所有真正改 LifeEngine 状态的 action 仍通过 `LifeEngineRuntime`，不直接写表。

## Avatar State Mapper

输入：

```text
agent_realtime_state
current_event
sleep_day_state
review_items
delayed_replies
```

输出：

```text
sprite_state
label
bubble
scene
```

状态映射：

```text
asleep/napping     → sleep
mode=dreaming      → dream
waiting_to_reply   → reply
uninterruptible    → battle
work/study event   → work
health/travel      → walk
meal               → eat
high fatigue       → tired
else               → idle
```

## 启动方式

```bash
hermes lifeengine webui --open
```

或：

```bash
python -m lifeengine.webui.server --life-dir ~/.hermes/lifeengine --open
```

## 版本

```text
Plugin version: 0.12.0
DB schema version: 39
sqlite-vec: required by LifeEngine runtime
WebUI: FastAPI + static React-free JS/CSS
```
