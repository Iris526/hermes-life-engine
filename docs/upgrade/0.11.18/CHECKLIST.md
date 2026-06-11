# 升级后检查清单

## 必跑

```bash
hermes plugins enable lifeengine
hermes lifeengine doctor
hermes lifeengine upgrade check --include-details
hermes lifeengine trace verify
```

## 推荐

```bash
hermes lifeengine upgrade verify_memory
hermes lifeengine upgrade package_check
hermes lifeengine review managed_observability
hermes lifeengine review managed_readiness
```

## 真实使用前

```text
/life review
/life policy conflicts
/life doctor
```

## 开启 Agent Managed Review Loop 前

```text
/life review managed_acceptance
/life review managed_stress 25
/life review managed_observability
/life review managed_readiness
```

只有 readiness 不是 blocked 时，才考虑开启自动管理。

## 快速回滚

```bash
rm -rf ~/.hermes/plugins/lifeengine
cp -a ~/.hermes/backups/lifeengine-plugin-v0.10.0-<timestamp> ~/.hermes/plugins/lifeengine
```

如果数据库已迁移，不建议只回滚代码；应恢复升级前 profile 备份。
