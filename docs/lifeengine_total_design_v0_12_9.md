# LifeEngine v0.12.9 — Outfit Resolver V2 / Collection Board

## 1. Collection / Item Norm

LifeEngine now treats **Collection** as the general container concept and **Item** as the concrete object.

- `item_collections`: a cabinet / drawer / shelf / container. Examples: 衣橱, 鞋柜, 袜子抽屉, 配饰柜, 梳妆台, 武器柜.
- `collection_items`: an actual item in a collection. Examples: 浅蓝短上衣, 白短靴, 铜铃, 侧编发方案.
- `collection_item_assets`: asset-generation jobs or fulfilled image assets for an item.
- `outfit_presets`: a named reusable outfit composition, e.g. “浅蓝那套”.
- `outfit_resolutions`: the result of resolving a natural request into item refs.
- `outfit_snapshots`: what the Agent is currently wearing.

Preset collections are defaults only. Humans or Agents may create, edit, archive, or rename collections.

## 2. Outfit Resolver V2 Priority

The v0.12.8 resolver was mainly token-based. v0.12.9 adds a deterministic priority chain:

1. Outfit preset exact name.
2. Outfit preset alias.
3. Item exact name.
4. Item alias.
5. Context-aware ranking using current activity, occasion, weather, season, style tags.
6. Token heuristic fallback.

This means requests like “穿浅蓝那套” should resolve to a preset or alias before fuzzy token matching.

## 3. Alias Support

Each item can have aliases:

- “浅蓝那套”
- “委托短靴”
- “日常铜铃”

Aliases are stored in `collection_item_aliases` and are private LifeEngine data. They are used by resolver and WebUI display.

## 4. Outfit Presets

An outfit preset is a named set of refs across collections.

Example:

```json
{
  "name": "浅蓝那套",
  "aliases": ["浅蓝套装", "淡蓝日常"],
  "item_refs": {
    "wardrobe": {"item_id": "..."},
    "shoe_cabinet": {"item_id": "..."},
    "sock_drawer": {"state": "bare_legs"},
    "accessory_cabinet": {"item_id": "..."},
    "vanity": {"item_id": "..."}
  }
}
```

## 5. Current Activity Context

Resolver receives optional context:

- event_category
- activity_domain
- occasion
- weather
- mood
- season
- style_tags

The resolver boosts items whose tags/attributes/materials match context. It still does not invent missing items.

## 6. WebUI Collection Board

The WebUI now has a dedicated “合集” board. It shows:

- every collection
- item count
- available count
- asset pending count
- item aliases
- asset completeness counts
- outfit presets

The board is human-facing and should not expose raw JSON by default.

## 7. Human-Facing Principle

Humans see:

- Collection names
- Item names
- aliases
- availability/cleanliness
- asset status
- outfit presets

Agents use tools for structured operations. Humans should not inspect raw DB rows.

## 8. New Actions

`life_collection` additions:

- `add_alias`
- `aliases`
- `outfit_presets`
- `create_outfit_preset`
- `update_outfit_preset`
- `archive_outfit_preset`
- `resolver_explain`

`life_interface` collection domain now includes aliases and outfit presets.
