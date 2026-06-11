# LifeEngine v0.10.0 → v0.11.18 升级 Patch 包

这个包用于把已经安装的 Hermes LifeEngine 插件从 `v0.10.0` 升级到 `v0.11.18`。

它包含两种升级材料：

1. **推荐方式：overlay 升级脚本**  
   `scripts/apply_patch.py` 会先备份当前 `~/.hermes/plugins/lifeengine`，再用 `overlay/lifeengine` 覆盖安装新插件代码。

2. **审阅方式：unified diff**  
   `patch/lifeengine_0.10.0_to_0.11.18.patch` 是从 v0.10.0 到 v0.11.18 的完整文本 diff，适合 code review，不建议手工逐段应用。

升级不会直接覆盖 LifeEngine 的 SQLite 数据库。数据库 schema 会在新插件首次运行时通过内置 migration 从 v0.10.0 线升级到 v0.11.18 线。

## 快速升级

```bash
unzip lifeengine_0_10_0_to_0_11_18_patch_package.zip
cd lifeengine_0_10_0_to_0_11_18_patch

python scripts/apply_patch.py
hermes plugins enable lifeengine
hermes lifeengine doctor
hermes lifeengine upgrade check --include-details
hermes lifeengine trace verify
```

## 包内容

```text
README.md
UPGRADE_FROM_0_10_0.md
DESIGN_PHILOSOPHY.md
HUMAN_MANUAL.md
AGENT_TOOL_GUIDE.md
CHANGELOG_0_10_0_TO_0_11_18.md
CHECKLIST.md
manifest.json
checksums.sha256
patch/lifeengine_0.10.0_to_0.11.18.patch
overlay/lifeengine/...
scripts/apply_patch.py
docs/lifeengine_design_v0_11_18.md
reference/lifeengine_design_v0_10_0.md
```

## 版本

- From plugin: `0.10.0`
- To plugin: `0.11.18`
- Target schema: `38`
- sqlite-vec: required
- Hermes integration: directory plugin; no core-loop fork
