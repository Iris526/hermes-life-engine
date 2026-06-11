# 从 LifeEngine v0.10.0 升级到 v0.11.18

## 1. 升级前备份

建议先备份 Hermes profile：

```bash
export HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
cp -a "$HERMES_HOME" "$HERMES_HOME.backup-before-lifeengine-0.11.18.$(date +%Y%m%d-%H%M%S)"
```

也可以先让旧插件导出 LifeEngine DB：

```bash
hermes lifeengine upgrade export
```

## 2. 应用 patch

推荐使用 overlay 脚本：

```bash
python scripts/apply_patch.py
```

可选参数：

```bash
python scripts/apply_patch.py --hermes-home ~/.hermes
python scripts/apply_patch.py --plugin-dir ~/.hermes/plugins/lifeengine
python scripts/apply_patch.py --force
python scripts/apply_patch.py --dry-run
```

脚本会执行：

```text
1. 定位 Hermes home。
2. 检查当前插件版本，默认期望 0.10.0。
3. 将旧插件目录备份到 $HERMES_HOME/backups/。
4. 复制 overlay/lifeengine 到 $HERMES_HOME/plugins/lifeengine。
5. 运行 Python 编译检查。
6. 打印后续 doctor / migration / trace 验证命令。
```

## 3. 安装依赖

v0.11.18 继续强依赖 sqlite-vec：

```bash
pip install -r overlay/requirements.txt
```

如果你的环境已经安装过，可以跳过。

## 4. 验证升级

```bash
hermes plugins enable lifeengine
hermes lifeengine doctor
hermes lifeengine upgrade check --include-details
hermes lifeengine trace verify
```

推荐额外跑一次：

```bash
hermes lifeengine upgrade package_check
hermes lifeengine upgrade verify_memory
hermes lifeengine review managed_observability
hermes lifeengine review managed_readiness
```

## 5. 回滚方式

如果插件代码升级后无法加载，使用脚本输出的备份目录恢复：

```bash
rm -rf ~/.hermes/plugins/lifeengine
cp -a ~/.hermes/backups/lifeengine-plugin-v0.10.0-<timestamp> ~/.hermes/plugins/lifeengine
```

如果数据库 schema 已经迁移，代码回滚不一定能读取新 schema。稳妥做法是恢复升级前的整个 Hermes profile 备份，或者使用 v0.11.18 的 `upgrade restore` 路线进行 staged restore。

## 6. 人类命令面变化

v0.11.18 之后普通用户不需要记大量命令，只需要：

```text
/life
/life setup <设定>
/life commit
/life pause
/life resume
/life run
/life call
/life dream
/life policy
/life review
/life doctor
/life backup
/life advanced
```

复杂能力交给 Agent 通过 `life_*` tools 自己使用。
