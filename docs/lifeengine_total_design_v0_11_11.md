# LifeEngine 总设计文档 v0.11.11 — Sleep / Reply / Dream 策略验收、冲突检查与导入导出

Plugin version: `0.11.11`  
Schema version: `31`  
sqlite-vec: required  
Hermes integration: directory plugin, no core-loop fork

## 1. 版本定位

v0.11.11 不新增新的生活大模块，而是把 v0.11.10 的 Sleep / Reply / Dream Policy UX 做到可验收、可冲突检测、可导出、可导入、可迁移。

核心目标：

```text
策略不是一段说明文字。
策略必须能被验证、能发现冲突、能导出备份、能导入迁移、能通过验收证明真的影响 Sleep / Reply / Dream / Autonomy / Execution。
```

## 2. 新增能力

### 2.1 Policy conflict check

新增冲突检查：

```text
life_policy(action="conflicts")
/life policy conflicts
hermes lifeengine policy conflicts
```

它会检查：

```text
睡眠目标时长是否合法
睡眠时间窗是否为 HH:MM
睡眠延迟上限是否过大
通宵恢复策略和 nap 设置是否冲突
ReplyGate auto/strict 是否保留 call words
ReplyGate auto/strict 是否允许 call override
不可打断 / 睡眠 / waiting_to_reply lease 是否有效
延迟回复 digest 模板是否包含 {count}/{summary}
Dream auto_safe repair 是否开启 audit_on_dream
Dream 关闭时是否错误开启 share_on_wake
内部 gate/audit 报告是否被默认展示给用户
```

冲突分两层：

```text
conflict:
  可能导致状态机错误、回复死锁或策略不可执行，必须修。

warning:
  技术上可执行，但 UX 或行为风险较高。
```

### 2.2 Policy conflict reports

新增表：

```text
sleep_reply_dream_policy_conflict_reports
```

每次检查会记录：

```text
status
conflict_count
warning_count
conflicts_json
warnings_json
policy_profile
policy_hash
created_at
```

这样策略诊断也进入 trace/audit 体系。

### 2.3 Policy export/import

新增：

```text
life_policy(action="export")
life_policy(action="import", path="...", apply=true)
life_policy(action="inspect_export", path="...")
```

导出为 JSON：

```json
{
  "manifest": {
    "kind": "lifeengine_sleep_reply_dream_policy",
    "plugin_version": "0.11.11",
    "policy_version": 1,
    "profile": "night_owl",
    "policy_hash": "..."
  },
  "policy": {...},
  "raw_policy": {...}
}
```

导入默认只 inspect。只有 `apply=true` 且无 hard conflict 时才应用。

新增表：

```text
sleep_reply_dream_policy_exports
sleep_reply_dream_policy_imports
```

### 2.4 Policy acceptance runner

新增：

```text
life_policy(action="acceptance")
/life policy acceptance
hermes lifeengine policy acceptance
```

使用 synthetic owner：

```text
<real_owner_id>-pol-<acceptance_run_id>
```

不污染真实 Agent 生活或真实 policy。

验收场景：

```text
POL01_PRESETS_VALIDATE
  所有内置 preset 无 hard conflict。

POL02_NIGHT_OWL_AFFECTS_SLEEP_PLAN
  night_owl preset 会影响 plan_day 默认睡眠时间。

POL03_PRIVATE_DREAM_NO_SHARE
  private preset 会关闭梦醒分享，share_mode=self_journal。

POL04_GENTLE_NAP_THRESHOLD
  gentle preset 会降低 recovery nap 触发阈值。

POL05_CONFLICT_DETECTION
  非法自定义策略会产生 conflict report。

POL06_EXPORT_IMPORT_ROUNDTRIP
  policy export/import 可迁移到另一个 owner。
```

新增表：

```text
sleep_reply_dream_policy_acceptance_runs
sleep_reply_dream_policy_acceptance_scenarios
```

## 3. 人类命令面

普通人仍然只需要：

```text
/life policy
/life policy explain
/life policy suggestions
/life policy conflicts
/life policy preset night_owl
/life policy export
/life policy import <path> apply
/life policy acceptance
```

高级内部工具仍然交给 Agent 使用，不要求人类记住。

## 4. Agent 工具面

`life_policy` 新增 action：

```text
conflicts
check_conflicts
validate
conflict_report
conflict_reports
list_conflicts
export
export_policy
exports
list_exports
inspect_import
inspect_export
import
import_policy
imports
list_imports
acceptance
policy_acceptance
srd_policy_acceptance
acceptance_runs
policy_acceptance_runs
acceptance_get
policy_acceptance_get
```

## 5. 闭环判断

v0.11.11 后，Sleep / Reply / Dream 策略层具备以下闭环：

```text
policy preset / patch
  ↓
conflict check
  ↓
audit report
  ↓
acceptance runner
  ↓
export/import
  ↓
policy migration / reuse
```

它解决的是：

```text
策略可读但不可验证
策略能调但不知道有没有冲突
策略能用但不能迁移
preset 改了但不知道是否真的影响行为
```

## 6. 当前状态

截至 v0.11.11：

```text
Event V2: 已实现
Realtime State: 已实现
SleepPlan/SleepSession: 已实现
ReplyGate / delayed replies / life_call: 已实现
DreamRun / DreamAudit / DreamEntry: 已实现
DreamAudit repair application: 已实现
Sleep debt / all-nighter / recovery plan: 已实现
Sleep-aware Autonomy: 已实现
Sleep-aware Execution: 已实现
Sleep/Reply/Dream conversation acceptance: 已实现
Sleep/Reply/Dream policy UX: 已实现
Policy conflicts / export/import / acceptance: 已实现
```

## 7. 下一步建议

下一步建议做 v0.11.12：

```text
Human UX polishing for policy and review
- /life review 聚合 policy conflicts、pending delayed replies、dream share intent、sleep debt
- Agent-facing action suggestions
- 一键 apply safe policy suggestions
- policy conflict 修复草案
```

