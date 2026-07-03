# LifeEngine v0.12.6 — Wardrobe / Cabinets Patch Design

## 1. Goal

This patch adds a **human-readable, extensible personal-collection system** to LifeEngine.

The first-class collections are:

- 衣橱（wardrobe）
- 鞋柜（shoe_cabinet）
- 梳妆台（vanity）
- 配饰柜（accessory_cabinet）
- 袜子抽屉（sock_drawer）

And the model is **extensible** so the Agent can define new collections, for example:

- 武器柜（weapon_cabinet）
- 道具柜（tool_cabinet）
- 药品柜（medical_cabinet）
- 书架（bookshelf）

Core rule:

> 每一件可穿戴/可使用物品，都必须先入库到对应集合；
> 真正穿衣、搭配、出门、化妆、换发型时，必须先从对应集合中检索并组装；
> 新增物品时必须按照该集合的“入库生图规则”生成该物品本体的素材图。

---

## 2. Core Philosophy

### 2.1 物与人分离

衣物、鞋子、袜子、配饰、妆容、发型等的资产图，**默认不是穿在身上的效果图**，而是：

- 物品本体图
- 三视图 / 多视图
- 材质说明
- 必要时包含细节局部图

即：**先存“资产”，再在穿搭事件中引用资产。**

### 2.2 收藏品先入库，再消费/穿戴

- 买了新衣服 → 生成衣服资产图 → 入衣橱
- 需要穿衣服 → 从衣橱取衣物、从鞋柜取鞋、从袜子抽屉取袜子、从配饰柜取配饰、从梳妆台取妆容/发型方案
- 如果没有可用物品 → 触发购买 / 洗护 / 修补 / 重新生成方案

### 2.3 集合规则可扩展

每个物品集合都有自己的：

- collection_name
- collection_type
- entry_image_rule（入库生图规则）
- checkout_rule（出库/使用规则）
- return_rule（归还/回库规则）
- maintenance_rule（保养/洗护/修复规则）

这让 Agent 可自行创建新的物品集合，并为它定义规则。

---

## 3. New Concepts

### 3.1 Collection

表示一个物品集合，例如衣橱、鞋柜。

Suggested fields:

- id
- owner_kind / owner_id
- collection_type
- name
- description
- status
- rules_json
- image_generation_rule_json
- usage_rule_json
- maintenance_rule_json
- created_at / updated_at

Recommended built-in collection_type:

- wardrobe
- shoe_cabinet
- vanity
- accessory_cabinet
- sock_drawer
- custom

### 3.2 Collection Item

表示集合中的一个具体条目。

Suggested fields:

- id
- collection_id
- item_type
- name
- description
- status
- tags_json
- attributes_json
- material_spec_json
- care_spec_json
- asset_bundle_json
- usage_state_json
- quantity
- condition_score
- cleanliness_state
- availability_state
- created_at / updated_at

### 3.3 Asset Bundle

一个条目绑定的一组素材资产。

Suggested fields (inside JSON or separate table):

- primary_image
- front_view
- side_view
- back_view
- detail_views[]
- material_sheet
- notes
- generated_from_rule
- prompt_snapshot
- created_at

### 3.4 Outfit / Styling Plan

穿搭时临时组装的方案。

Suggested fields:

- id
- owner_kind / owner_id
- occasion
- top_item_id
- bottom_item_id / onepiece_item_id
- shoes_item_id
- socks_item_id
- accessory_item_ids[]
- makeup_item_id / makeup_recipe_id
- hairstyle_item_id / hairstyle_recipe_id
- status
- notes
- created_at

---

## 4. Built-in Collections Specification

### 4.1 衣橱 wardrobe

#### 4.1.1 存什么

- 上衣
- 下装
- 连衣裙 / 套装
- 外套
- 家居服
- 睡衣
- 运动服
- 内搭

#### 4.1.2 入库生图规则

每件衣物必须生成：

- 正视图
- 侧视图
- 背视图
- 平铺图或挂拍图（可选，但建议）
- 材质说明图 / 材质卡

规则要求：

- **只画衣服本体，不画穿在人身上**
- 背景尽量简洁统一
- 明确服装轮廓、长度、版型
- 写明面料、颜色、季节属性、风格标签

Recommended metadata:

- category: top / bottom / dress / outerwear / loungewear / sleepwear
- silhouette
- color_family
- season
- style_tags
- layer_level
- formalness
- warmth
- wash_cycle

#### 4.1.3 出入库规则

- 新买 / 新做 / 新获得 → 先生成资产图 → create_collection_item → status=available
- 穿着时 → check_out / reserve
- 穿完后 → return
- 若脏了 → cleanliness_state=dirty，不能直接再次穿
- 洗护完成 → cleanliness_state=clean
- 损坏 → condition_score 下降，必要时进入 repair event

---

### 4.2 鞋柜 shoe_cabinet

#### 存什么

- 日常鞋
- 靴子
- 凉鞋
- 运动鞋
- 室内鞋
- 雨鞋

#### 入库生图规则

每双鞋必须生成：

- 正侧展示图（左/右）
- 俯视图
- 后跟图
- 鞋底图（建议）
- 材质说明图

要求：

- 不穿在人脚上
- 明确鞋型、鞋跟、鞋底、材质、用途

#### 使用规则

- 穿搭时必须从鞋柜选
- 若天气/地面条件不合适，可过滤（例如雨天优先防水鞋）
- 使用后可能进入 dirty / airing / repair

---

### 4.3 袜子抽屉 sock_drawer

#### 存什么

- 短袜
- 长袜
- 连裤袜
- 保暖袜
- 运动袜
- 居家袜

#### 入库生图规则

每种袜子生成：

- 正视平铺图
- 背视平铺图
- 材质/厚度说明图

要求：

- 不画穿在脚上
- 重点是长度、图案、厚薄、材质

#### 使用规则

- 与鞋和服装搭配
- 可按数量管理
- 脏袜使用后进入 laundry

---

### 4.4 配饰柜 accessory_cabinet

#### 存什么

- 发饰
- 项链
- 耳饰
- 手链 / 手表
- 戒指
- 腰带
- 包
- 披肩
- 特殊身份配件（护符、铜铃、罗盘挂件）

#### 入库生图规则

每件配饰生成：

- 主展示图
- 正/侧/背视图（依配饰种类而定）
- 材质说明图
- 细节局部图（例如花纹、吊坠）

要求：

- 单品为主
- 不默认画佩戴效果

#### 使用规则

- 可叠加使用
- 需要跟 outfit / occasion 匹配
- 特殊道具配饰可影响 event flavor / worldview identity

---

### 4.5 梳妆台 vanity

梳妆台不是只有“物件”，还包含**可复用方案**。

#### 存什么

- 妆容方案（makeup look）
- 发型方案（hairstyle preset）
- 彩妆单品（可选）
- 护肤/整理工具（可选）

#### 入库生图规则

妆容方案：

- 面部前视图
- 侧视图（可选）
- 重点细节图（眼妆/唇妆/腮红）
- 用色和质感说明

发型方案：

- 正视图
- 侧视图
- 背视图
- 发饰结合示意（可选）

要求：

- 妆容/发型方案可以使用“头模 / face chart / hairstyle sheet”风格
- 不强制必须是完整人物穿搭图

#### 使用规则

- 梳妆时从 vanity 选择发型与妆容方案
- 方案可与衣橱/配饰组合成完整 look
- 新方案入库时必须生成对应方案图

---

## 5. Generalized Collection Extension

Agent can create a new collection by defining:

- name
- collection_type=custom
- display_name
- purpose
- entry_image_rule
- usage_rule
- maintenance_rule
- required_metadata

Example: 武器柜 weapon_cabinet

Entry image rule:

- 主展示图
- 正视图
- 侧视图
- 细节图
- 材质/尺寸/重量说明

Usage rule:

- 出任务时可以 checkout
- 使用后进入 clean / repair / recharge / storage

---

## 6. Wear / Styling Flow

When Agent needs to dress:

1. 读取当前场景条件
   - 天气
   - 时间
   - 今日 event / occasion
   - mood / energy / fatigue
   - cleanliness / availability
2. 从衣橱选衣
3. 从鞋柜选鞋
4. 从袜子抽屉选袜子
5. 从配饰柜选配饰
6. 从梳妆台选发型 / 妆容
7. 生成 Outfit / StylingPlan
8. 将相关 item 标记为 reserved / in_use
9. 事件结束后执行 return / dirty / maintenance

Important:

- 如果集合中无匹配条目，则触发“买新物 / 生成新方案 / 清洗可复用物”的事件，而不是凭空穿出一件不存在的衣服。

---

## 7. Inbound / Outbound / Maintenance Rules

### Inbound 入库

- acquire / buy / craft / gift
- 必须生成该集合要求的资产图
- 必须写 material_spec
- 必须填写 category-specific metadata
- 状态变为 available

### Checkout / Reserve 出库

- 由 outfit / event / schedule 触发
- item availability_state: reserved 或 in_use

### Return 回库

- 事件结束后归还
- 可进入 clean / dirty / airing / repair_needed

### Maintenance 保养

- 清洗
- 晾晒
- 充能 / 补妆 / 修理
- condition_score 变化

---

## 8. Suggested Commands / API Surface

### Human-friendly commands

- `/life closet`
- `/life closet wardrobe`
- `/life closet shoes`
- `/life closet socks`
- `/life closet accessories`
- `/life closet vanity`
- `/life closet create_collection 武器柜`
- `/life closet add wardrobe <描述>`
- `/life closet add shoes <描述>`
- `/life closet outfit today`

### Agent tools

New tool recommendation: `life_collection`

Actions:

- summary
- collections
- get_collection
- create_collection
- define_rules
- items
- get_item
- add_item
- generate_assets
- check_out
- return_item
- mark_dirty
- maintain
- build_outfit
- get_outfit

### `life_interface` domain extension

Add `domain = collection` with views/intents:

Read:
- summary
- collections
- wardrobe
- shoes
- socks
- accessories
- vanity
- items
- outfit

Write:
- create_collection
- add_item
- generate_assets
- check_out
- return_item
- build_outfit
- maintain

---

## 9. Suggested Schema Additions

Recommended tables:

- item_collections
- collection_items
- collection_item_assets
- outfit_plans
- collection_rule_presets
- collection_maintenance_runs

Optional:
- collection_usage_history
- collection_generation_jobs

---

## 10. Review / Human Display

For humans, display should be readable.

Examples:

### `/life closet wardrobe`

```text
衣橱
====
1. 白色短上衣（clean / available）
   材质：棉混纺；季节：夏 / 春；风格：简洁、轻灵
2. 黑色长裙（clean / available）
   材质：垂感针织；季节：四季；风格：冷感、修长
```

### `/life closet outfit today`

```text
今日穿搭建议
============
上衣：白色短上衣
下装：黑色长裙
鞋：黑色短靴
袜子：中筒黑袜
配饰：红绳发饰、铜铃挂件
发型：双丸子头（简洁版）
妆容：淡色眼妆 + 自然唇色
原因：今天有轻度外出委托，天气偏凉，体力一般，适合轻便但不失身份感的搭配。
```

---

## 11. Rule Templates

### Wardrobe entry image rule template

- subject: clothing item only
- views: front, side, back
- optional: flat lay
- include: material sheet, color tags, silhouette notes
- exclude: full-body worn styling by default

### Shoe cabinet entry image rule template

- subject: shoes only
- views: side, top, back, sole
- include: material / weather suitability

### Vanity entry image rule template

- subject: makeup look board or hairstyle sheet
- views: front / side / back as needed
- include: cosmetic palette or styling notes

---

## 12. Default Operational Principle

- LifeEngine **must not conjure wearable items from nothing**.
- Every wearable/usable style asset should first exist in a collection.
- New items require asset generation using the collection's rule.
- Dressing is a retrieval + composition process.
- The model is extensible for any new collection category.


---

## 13. v0.12.6 Implementation Notes

This release implements the collection system as code.

### Implemented tables

- `item_collections`
- `collection_items`
- `collection_item_assets`
- `outfit_plans`
- `collection_rule_presets`
- `collection_maintenance_runs`
- `collection_usage_history`

### Implemented tool

- `life_collection`

### Implemented human commands

- `/life closet`
- `/life closet init`
- `/life closet wardrobe`
- `/life closet shoes`
- `/life closet socks`
- `/life closet accessories`
- `/life closet vanity`
- `/life closet add <collection_type> <name> <description>`
- `/life closet outfit`

### Editable presets

The built-in categories are only defaults.  They can be created, renamed,
updated, archived, and replaced.  An Agent can define a new collection such as
`weapon_cabinet` with custom `image_generation_rule`, `usage_rule`, and
`maintenance_rule`.

### Image generation jobs

When adding a new item, LifeEngine creates pending asset-generation jobs for the
views required by the collection rule.  The image pipeline can fulfill these jobs
by attaching `asset_uri` to each asset entry.  This avoids pretending that the
text runtime itself already generated an image.

