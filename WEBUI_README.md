# LifeEngine WebUI / Observatory v0.12.0

## 启动

```bash
hermes lifeengine webui --open
```

指定 LifeEngine 目录或数据库：

```bash
hermes lifeengine webui --life-dir ~/.hermes/lifeengine --open
hermes lifeengine webui --life-dir ~/.hermes/lifeengine/lifeengine.db --open
```

或者直接作为 Python 模块运行：

```bash
python -m lifeengine.webui.server --life-dir ~/.hermes/lifeengine --host 127.0.0.1 --port 8765 --open
```

## 页面

- 像素 Agent 小人：状态由 `agent_realtime_state`、当前 event、sleep day state、delayed replies 映射。
- 实时状态：当前 mode、active event、资源、睡眠债、恢复压力。
- 日程：今天 / 明天 / 本周 / 指定日期 timeline。
- Review：人类可读 item 列表。
- 资源、梦境、最近流水。

## 目录选择

WebUI 顶部输入框可以选择：

- LifeEngine 目录，包含 `lifeengine.db`
- Hermes profile 目录，包含 `lifeengine/lifeengine.db`
- 直接选择 `lifeengine.db`

任意外部 DB 默认只读。只有当前 Hermes profile 的 LifeEngine DB 才允许有限 operator action。

## Operator action

WebUI 目前提供：

- Call：强制叫醒 / 打断 / 恢复对话
- 刷新
- 安全处理：按 Review Action Policy 处理低风险 review items

这些 action 不直接写表，全部通过 `LifeEngineRuntime`。

## 默认形象

`lifeengine/webui/static/assets/default-agent-reference.jpg` 来自用户提供的参考图。`default-agent-pixel.png` 是从参考图裁剪、低分辨率像素化后生成的默认舞台形象。
