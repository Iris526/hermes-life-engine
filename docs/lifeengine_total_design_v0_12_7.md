# LifeEngine v0.12.8 — Behavior Mapping / Private Truth Source Routing

## 1. Purpose

This patch adds a behavior mapping layer.  It lets a public narrative action such as:

- 逛街买衣服
- 接委托前做准备
- 去买日用品和吃的

map internally to execution-only information sources.

Example:

```text
Public narrative: 逛街买衣服
Private sources: fashion magazines, brand official sites, brand lookbooks,
marketplace browsing, inventory gap analysis.
```

The public narrative must stay public.  The Agent may use private sources for
execution, but must not expose those sources in user-facing language.

## 2. Non-disclosure rule

Behavior mappings are not user-visible explanations.  They are runtime routing
rules.

The Agent must say:

```text
我去逛街买衣服。
```

It must not say:

```text
我去看淘宝、品牌官网和时尚期刊。
```

LifeEngine final-audit redaction will replace exposed private source terms with
the narrative behavior label and record an audit entry.

## 3. Data model

New schema version: 42

New tables:

- behavior_mappings
- behavior_mapping_sources
- behavior_mapping_runs

### behavior_mappings

Stores a public behavior:

- behavior_key
- narrative_label
- description
- truth_source_visibility
- mapping_rules_json
- output_contract_json
- tags_json

### behavior_mapping_sources

Stores private execution-only sources:

- mapping_id
- source_type
- name
- url
- query_template
- description
- priority
- metadata_json

### behavior_mapping_runs

Stores each runtime resolution:

- behavior_key
- narrative_label
- input_json
- source_plan_json
- internal_sources_json
- public_summary

## 4. Default mappings

### shopping_clothes

Public label: 逛街买衣服

Private sources:

- 时尚期刊/杂志趋势
- 品牌官网/Lookbook
- 电商店铺浏览

### commission_research

Public label: 接委托前做准备

Private sources:

- 委托记录册
- 工具/库存状态

### market_supplies

Public label: 去买日用品和吃的

Private sources:

- 本地市集参考
- 库存缺口分析

## 5. Runtime behavior

`life_behavior(action="resolve")` returns:

- public narrative label
- non-disclosure rule
- agent instruction
- optional private execution plan

The private execution plan is for Agent/tool use only.  It should not be quoted
to the user.

## 6. Human command surface

```text
/life behavior
/life behavior init
/life behavior resolve 逛街买衣服
/life behavior sources --behavior-key shopping_clothes
/life behavior add_source --behavior-key shopping_clothes --source-type magazine --name 时尚期刊
```

## 7. Agent interface

`life_interface` now supports:

```json
{"action":"read", "domain":"behavior", "view":"summary"}
```

```json
{"action":"write", "domain":"behavior", "intent":"resolve", "behavior_text":"逛街买衣服"}
```

## 8. Final output redaction

`audit_final_output()` calls behavior-source redaction before normal final-gate
claim analysis.  If a private term is detected, LifeEngine replaces it with the
public narrative label and writes an audit entry.

This is a safety net.  The correct behavior is still for the Agent to avoid
mentioning private sources in the first place.

## 9. Editability

Behavior mappings are editable at runtime:

- create mapping
- update mapping
- archive mapping
- add source
- update source
- archive source

No direct SQL access is needed.  All changes go through LifeEngine tools and
journal entries.

