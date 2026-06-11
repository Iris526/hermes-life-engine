# LifeEngine 人类使用手册

## 最常用命令

```text
/life                      查看状态
/life setup <设定>         进入或继续设定
/life commit               提交设定草案
/life pause                暂停 LifeEngine
/life resume               恢复 LifeEngine
/life run                  手动 heartbeat 一次
/life call                 强制唤醒 / 强制打断，防卡死
/life dream                查看或运行梦系统
/life policy               查看睡眠/回复/梦策略
/life review               查看待处理总览
/life doctor               健康检查
/life backup               导出备份
/life advanced             查看高级命令
```

## 建议日常流程

### 初次设定

```text
/life setup 你是一个和我同城生活的 Agent，天气参考我所在地，货币用日元。你需要睡觉、吃饭、休息，有钱包、精力、心情、灵感值和衣柜。梦可以醒来后分享，但默认先进入 pending。
/life commit
/life resume
```

### 查看是否有待处理事项

```text
/life review
```

你会看到：

```text
睡眠债
延迟回复
梦分享
策略冲突
用户侧确认
主动消息
Doctor warning
FinalGate advisory
```

### 应用一条建议

```text
/life review preview <item_id>
/life review apply <item_id>
```

### 批量处理安全建议

```text
/life review batch_preview reply
/life review apply_all reply
```

### 强制唤醒 / 防卡死

```text
/life call
```

这个命令无论 Agent 在睡觉、不可打断事件、等待回复还是状态异常，都会强制打断并释放回复路径。

## FinalGate 行为

默认是 advisory：

```text
不会把内部拦截报告发给用户。
不会硬拦普通计划/意图。
会写 trace 和内部反馈，提示 Agent 下一轮修正。
```

## Managed Review Loop

默认不开启。开启前建议检查：

```text
/life review managed_acceptance
/life review managed_stress
/life review managed_observability
/life review managed_readiness
```

只有 readiness 结果足够安全时，再考虑在 policy 中允许 Agent 自动处理 safe review items。
