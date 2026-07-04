"""Collection / closet system for LifeEngine.

This module implements editable collection presets such as wardrobe, shoe
cabinet, vanity, accessory cabinet, and sock drawer.  It deliberately stores
asset-generation rules and asset generation jobs instead of pretending that a
text tool can directly create images.  The rule is: a new item must carry an
asset bundle plan, and the agent/image pipeline can later fulfill the pending
asset generation job.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .time_utils import now_iso


class CollectionError(ValueError):
    pass


DEFAULT_COLLECTION_PRESETS: dict[str, dict[str, Any]] = {
    "wardrobe": {
        "name": "衣橱",
        "description": "衣服本体资产集合：上衣、下装、连衣裙、外套、睡衣、家居服等。",
        "entry_image_rule": {
            "subject": "clothing_item_with_character_display",
            "views": [
                "item_front_view",
                "item_side_view",
                "item_back_view",
                "material_detail_sheet",
                "character_worn_display",
            ],
            "must": [
                "三视图只画衣服本体平铺/悬挂，不画穿在人身上",
                "材质细节展示面料、纹样、扣具、缝线",
                "角色试穿图展示 agent 角色穿着该衣服的全身画面，配合简洁背景",
            ],
            "exclude": [
                "三视图里出现人物身体",
            ],
            "character_ref": "agent_primary_reference",
            "layout_hint": "试穿图作为独立一张展示，与三视图分开",
        },
        "usage_rule": {
            "checkout_for": ["outfit", "sleepwear", "work", "travel", "daily_life"],
            "return_states": ["clean", "dirty", "airing", "repair_needed"],
            "cannot_use_when": ["dirty", "repair_needed", "archived"],
        },
        "required_metadata": ["category", "color_family", "season", "style_tags", "material", "warmth", "formalness"],
    },
    "shoe_cabinet": {
        "name": "鞋柜",
        "description": "鞋子本体资产集合：靴子、日常鞋、运动鞋、室内鞋、雨鞋等。",
        "entry_image_rule": {
            "subject": "shoe_pair_with_character_display",
            "views": [
                "shoe_side_view",
                "shoe_top_view",
                "shoe_sole_view",
                "shoe_heel_view",
                "material_detail_sheet",
                "character_worn_display",
            ],
            "must": [
                "三视图只画鞋子本体，明确展示鞋面、鞋底、鞋跟",
                "材质细节展示鞋面材质、鞋底纹路、鞋跟高度",
                "角色试穿图展示 agent 角色穿着该鞋的全身画面，可以看到鞋子的穿戴效果",
            ],
            "exclude": [
                "三视图里出现脚部/人物",
            ],
            "character_ref": "agent_primary_reference",
            "layout_hint": "试穿图作为独立一张展示，与三视图分开",
        },
        "usage_rule": {
            "checkout_for": ["outfit", "outdoor", "indoor", "rain"],
            "return_states": ["clean", "dirty", "airing", "repair_needed"],
            "weather_filter": True,
        },
        "required_metadata": ["shoe_type", "color_family", "weather_suitability", "material", "comfort", "season"],
    },
    "sock_drawer": {
        "name": "袜子抽屉",
        "description": "袜子集合：短袜、长袜、连裤袜、保暖袜、运动袜、居家袜。",
        "entry_image_rule": {
            "subject": "socks_with_character_display",
            "views": [
                "socks_flat_front",
                "socks_flat_back",
                "material_thickness_sheet",
                "character_worn_display",
            ],
            "must": [
                "平铺图只画袜子本体，展示长度、图案、厚薄",
                "角色试穿图展示 agent 角色穿着该袜子的腿部/脚部画面",
            ],
            "exclude": [
                "平铺图里出现脚部",
            ],
            "character_ref": "agent_primary_reference",
        },
        "usage_rule": {"quantity_managed": True, "return_states": ["laundry", "clean", "worn_out"]},
        "required_metadata": ["sock_type", "length", "thickness", "material", "color_family", "quantity_per_pair"],
    },
    "accessory_cabinet": {
        "name": "配饰柜",
        "description": "配饰集合：发饰、项链、耳饰、手链、腰带、包、披肩、护符、铜铃等。",
        "entry_image_rule": {
            "subject": "accessory_with_character_display",
            "views": [
                "accessory_main_display",
                "accessory_detail_view",
                "material_detail_sheet",
                "character_worn_display",
            ],
            "must": [
                "单品展示配饰本体，展示材质、吊坠/纹样/扣具细节",
                "角色佩戴图展示 agent 角色佩戴该配饰的画面",
            ],
            "exclude": [],
            "character_ref": "agent_primary_reference",
        },
        "usage_rule": {"stackable": True, "checkout_for": ["outfit", "ritual", "identity", "work"]},
        "required_metadata": ["accessory_type", "material", "color_family", "style_tags", "symbolic_meaning"],
    },
    "vanity": {
        "name": "梳妆台",
        "description": "妆容、发型、护肤/整理工具与可复用造型方案。",
        "entry_image_rule": {
            "subject": "makeup_or_hairstyle_with_character_display",
            "views": [
                "style_front_view",
                "style_side_view",
                "style_back_view",
                "detail_sheet",
                "character_worn_display",
            ],
            "must": [
                "发型必须展示正侧背三视图",
                "妆容可以用 face chart",
                "角色妆造图展示 agent 角色完成该妆造后的正面画面",
            ],
            "exclude": [],
            "character_ref": "agent_primary_reference",
        },
        "usage_rule": {"recipe_allowed": True, "checkout_for": ["makeup", "hairstyle", "daily_grooming", "occasion"]},
        "required_metadata": ["vanity_type", "style_tags", "palette", "hair_accessories", "time_cost_minutes"],
    },
    "supply_cabinet": {
        "name": "随身物品柜",
        "description": "日常消耗品、工具、委托或工作物资，用于记录可携带、可消耗或需维护的物品。",
        "entry_image_rule": {
            "subject": "supply_or_tool_item",
            "views": [
                "item_main_display",
                "item_detail_view",
                "material_detail_sheet",
            ],
            "must": [
                "单品展示，标注用途和材质",
                "消耗品展示外观和包装",
                "工具展示功能细节",
            ],
            "exclude": [],
        },
        "usage_rule": {
            "consumable": True,
            "checkout_for": ["ritual", "commission", "daily_life", "travel"],
            "return_states": ["clean", "dirty", "repair_needed"],
            "cannot_use_when": ["repair_needed", "archived"],
        },
        "required_metadata": ["category", "is_consumable", "material", "purpose"],
    },
}


def _row_to_collection(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("rules_json", "image_generation_rule_json", "usage_rule_json", "maintenance_rule_json", "required_metadata_json"):
        d[k.replace("_json", "")] = loads(d.pop(k), {} if k != "required_metadata_json" else [])
    return d


def _row_to_item(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("tags_json", "attributes_json", "material_spec_json", "care_spec_json", "asset_bundle_json", "usage_state_json"):
        d[k.replace("_json", "")] = loads(d.pop(k), {} if k not in {"tags_json"} else [])
    return d


def _row_to_asset(row) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = loads(d.pop("metadata_json"), {})
    return d


def get_display_image(item: dict[str, Any]) -> str | None:
    """封面/展示图：穿着立绘/全身图。为空时回退到 reference_image。"""
    bundle = item.get("asset_bundle") or {}
    return bundle.get("display_image") or bundle.get("reference_image")


def get_reference_image(item: dict[str, Any]) -> str | None:
    """参考图：complex asset sheet，穿搭生成用。为空时回退到 display_image。"""
    bundle = item.get("asset_bundle") or {}
    return bundle.get("reference_image") or bundle.get("display_image")


def _row_to_outfit(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("item_ids_json", "context_json", "reasoning_json"):
        d[k.replace("_json", "")] = loads(d.pop(k), [] if k == "item_ids_json" else {})
    return d


def ensure_default_collections(conn, owner_kind: str, owner_id: str, *, source: str = "life_collection") -> list[dict[str, Any]]:
    out = []
    for ctype, preset in DEFAULT_COLLECTION_PRESETS.items():
        row = conn.execute(
            "SELECT * FROM item_collections WHERE owner_kind=? AND owner_id=? AND collection_type=? AND status!='archived' ORDER BY created_at LIMIT 1",
            (owner_kind, owner_id, ctype),
        ).fetchone()
        if row:
            out.append(_row_to_collection(row)); continue
        out.append(create_collection(
            conn, owner_kind, owner_id,
            collection_type=ctype,
            name=preset["name"],
            description=preset.get("description"),
            image_generation_rule=preset.get("entry_image_rule"),
            usage_rule=preset.get("usage_rule"),
            required_metadata=preset.get("required_metadata"),
            source=source,
        ))
    return out


def list_collections(conn, owner_kind: str, owner_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM item_collections WHERE owner_kind=? AND owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if not include_archived:
        sql += " AND status!='archived'"
    sql += " ORDER BY sort_order, created_at"
    return [_row_to_collection(r) for r in conn.execute(sql, params).fetchall()]


def get_collection(conn, owner_kind: str, owner_id: str, collection_id: str | None = None, collection_type: str | None = None) -> dict[str, Any]:
    if collection_id:
        row = conn.execute("SELECT * FROM item_collections WHERE id=? AND owner_kind=? AND owner_id=?", (collection_id, owner_kind, owner_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM item_collections WHERE collection_type=? AND owner_kind=? AND owner_id=? AND status!='archived' ORDER BY created_at LIMIT 1", (collection_type, owner_kind, owner_id)).fetchone()
    if not row:
        raise CollectionError(f"collection not found: {collection_id or collection_type}")
    return _row_to_collection(row)


def create_collection(conn, owner_kind: str, owner_id: str, *, collection_type: str = "custom", name: str, description: str | None = None,
                      rules: dict[str, Any] | None = None, image_generation_rule: dict[str, Any] | None = None,
                      usage_rule: dict[str, Any] | None = None, maintenance_rule: dict[str, Any] | None = None,
                      required_metadata: list[str] | None = None, status: str = "active", sort_order: int = 100,
                      source: str = "life_collection") -> dict[str, Any]:
    if not name or not str(name).strip():
        raise CollectionError("collection name is required")
    if not description or not str(description).strip():
        raise CollectionError("collection description is required: explain what this collection holds")
    # image_generation_rule must have at least a views list so items know what to generate.
    if not image_generation_rule or not isinstance(image_generation_rule, dict):
        raise CollectionError("image_generation_rule is required: must define views and must/exclude rules for asset generation")
    views = image_generation_rule.get("views")
    if not views or not isinstance(views, list) or len(views) == 0:
        raise CollectionError("image_generation_rule.views is required: list at least one asset view (e.g. item_front_view, character_worn_display)")
    collection_id = new_id("collection")
    conn.execute(
        """INSERT INTO item_collections(id, owner_kind, owner_id, collection_type, name, description, status, rules_json,
             image_generation_rule_json, usage_rule_json, maintenance_rule_json, required_metadata_json, sort_order)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (collection_id, owner_kind, owner_id, collection_type or "custom", str(name).strip(), description, status,
         dumps(rules or {}), dumps(image_generation_rule or {}), dumps(usage_rule or {}), dumps(maintenance_rule or {}), dumps(required_metadata or []), int(sort_order)),
    )
    append_journal(conn, owner_kind, owner_id, "collection_created", {"collection_id": collection_id, "collection_type": collection_type, "name": name}, source)
    return get_collection(conn, owner_kind, owner_id, collection_id=collection_id)


def update_collection(conn, owner_kind: str, owner_id: str, *, collection_id: str | None = None, collection_type: str | None = None, source: str = "life_collection", **fields: Any) -> dict[str, Any]:
    c = get_collection(conn, owner_kind, owner_id, collection_id, collection_type)
    allowed = {"collection_type", "name", "description", "status", "sort_order"}
    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if k in allowed and v is not None:
            updates[k] = v
    json_map = {
        "rules": "rules_json",
        "image_generation_rule": "image_generation_rule_json",
        "usage_rule": "usage_rule_json",
        "maintenance_rule": "maintenance_rule_json",
        "required_metadata": "required_metadata_json",
    }
    for src, col in json_map.items():
        if src in fields and fields[src] is not None:
            updates[col] = dumps(fields[src])
    if not updates:
        return c
    sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
    conn.execute(f"UPDATE item_collections SET {sets} WHERE id=? AND owner_kind=? AND owner_id=?", tuple(updates.values()) + (c["id"], owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "collection_updated", {"collection_id": c["id"], "updates": updates}, source)
    return get_collection(conn, owner_kind, owner_id, collection_id=c["id"])


def archive_collection(conn, owner_kind: str, owner_id: str, *, collection_id: str | None = None, collection_type: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    return update_collection(conn, owner_kind, owner_id, collection_id=collection_id, collection_type=collection_type, status="archived", source=source)


def _asset_requirements_for_collection(collection: dict[str, Any]) -> list[str]:
    rule = collection.get("image_generation_rule") or {}
    views = rule.get("views") or []
    return [str(v) for v in views] or ["main_display", "material_sheet"]


def build_asset_generation_prompt(collection: dict[str, Any], item: dict[str, Any], view: str | None = None) -> str:
    """Build a prompt for generating an asset image for a collection item.

    The prompt is driven by the collection's entry_image_rule.  When the view
    is ``character_worn_display``, the prompt asks for the agent character
    wearing/holding the item; otherwise it asks for the item-only sheet.
    """
    rule = collection.get("image_generation_rule") or {}
    must = "; ".join(rule.get("must") or [])
    exclude = "; ".join(rule.get("exclude") or [])
    material = item.get("material_spec") or {}
    attrs = item.get("attributes") or {}
    view_text = f" View: {view}." if view else ""
    char_ref = rule.get("character_ref")
    is_worn = view == "character_worn_display"

    parts = [
        f"Create an asset image for {collection.get('name')} / {collection.get('collection_type')}: {item.get('name')}.",
        f" Subject rule: {rule.get('subject', 'item only')}.{view_text}",
    ]
    if is_worn and char_ref:
        parts.append(
            f" This is the CHARACTER WORN DISPLAY view: show the agent character wearing/holding "
            f"'{item.get('name')}' in a full-body composition with a clean background. "
            f"Use the agent primary reference image for character identity consistency. "
            f"Item details: {attrs}. Material: {material}."
        )
    else:
        parts.append(
            f" This is an ITEM-ONLY view (no person/body). "
            f"Description: {item.get('description') or ''}. Attributes: {attrs}. Material: {material}."
        )
    if must:
        parts.append(f" Must: {must}.")
    if exclude:
        parts.append(f" Exclude: {exclude}.")
    return " ".join(parts)


def create_collection_item(conn, owner_kind: str, owner_id: str, *, collection_id: str | None = None, collection_type: str | None = None,
                           name: str, item_type: str | None = None, description: str | None = None, tags: list[str] | None = None,
                           attributes: dict[str, Any] | None = None, material_spec: dict[str, Any] | None = None,
                           care_spec: dict[str, Any] | None = None, quantity: float = 1, condition_score: int = 100,
                           cleanliness_state: str = "clean", availability_state: str = "available",
                           source: str = "life_collection") -> dict[str, Any]:
    if not name or not str(name).strip():
        raise CollectionError("collection item name is required")
    collection = get_collection(conn, owner_kind, owner_id, collection_id, collection_type)
    item_id = new_id("colitem")
    asset_bundle = {
        "display_image": None,       # 封面/展示图：穿着立绘/全身图，视觉展示优先
        "reference_image": None,     # 参考图：complex asset sheet，穿搭生成/功能参考优先
        "status": "needs_generation",
        "generated_from_rule": collection.get("image_generation_rule"),
    }
    conn.execute(
        """INSERT INTO collection_items(id, owner_kind, owner_id, collection_id, item_type, name, description, status, tags_json,
             attributes_json, material_spec_json, care_spec_json, asset_bundle_json, quantity, condition_score, cleanliness_state, availability_state)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item_id, owner_kind, owner_id, collection["id"], item_type or collection.get("collection_type") or "item", name, description, "active",
         dumps(tags or []), dumps(attributes or {}), dumps(material_spec or {}), dumps(care_spec or {}), dumps(asset_bundle), float(quantity), int(condition_score), cleanliness_state, availability_state),
    )
    item = get_collection_item(conn, owner_kind, owner_id, item_id)
    append_journal(conn, owner_kind, owner_id, "collection_item_created", {"item_id": item_id, "collection_id": collection["id"], "name": name}, source)
    return get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True)


def get_collection_item(conn, owner_kind: str, owner_id: str, item_id: str, *, include_assets: bool = False) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM collection_items WHERE id=? AND owner_kind=? AND owner_id=?", (item_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise CollectionError(f"collection item not found: {item_id}")
    d = _row_to_item(row)
    if include_assets:
        d["assets"] = list_item_assets(conn, owner_kind, owner_id, item_id=item_id)
    return d


def list_collection_items(conn, owner_kind: str, owner_id: str, *, collection_id: str | None = None, collection_type: str | None = None,
                          status: str | None = "active", availability_state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT i.* FROM collection_items i JOIN item_collections c ON c.id=i.collection_id WHERE i.owner_kind=? AND i.owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if collection_id:
        sql += " AND i.collection_id=?"; params.append(collection_id)
    if collection_type:
        sql += " AND c.collection_type=?"; params.append(collection_type)
    if status:
        sql += " AND i.status=?"; params.append(status)
    if availability_state:
        sql += " AND i.availability_state=?"; params.append(availability_state)
    sql += " ORDER BY i.updated_at DESC LIMIT ?"; params.append(int(limit))
    return [_row_to_item(r) for r in conn.execute(sql, params).fetchall()]


def update_collection_item(conn, owner_kind: str, owner_id: str, *, item_id: str, source: str = "life_collection", **fields: Any) -> dict[str, Any]:
    current = get_collection_item(conn, owner_kind, owner_id, item_id)
    allowed = {"item_type", "name", "description", "status", "quantity", "condition_score", "cleanliness_state", "availability_state"}
    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if k in allowed and v is not None:
            updates[k] = v
    json_fields = {"tags": "tags_json", "attributes": "attributes_json", "material_spec": "material_spec_json", "care_spec": "care_spec_json", "asset_bundle": "asset_bundle_json", "usage_state": "usage_state_json"}
    for k, col in json_fields.items():
        if k in fields and fields[k] is not None:
            updates[col] = dumps(fields[k])
    if not updates:
        return current
    sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
    conn.execute(f"UPDATE collection_items SET {sets} WHERE id=? AND owner_kind=? AND owner_id=?", tuple(updates.values()) + (item_id, owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "collection_item_updated", {"item_id": item_id, "updates": updates}, source)
    return get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True)


def create_item_asset(conn, owner_kind: str, owner_id: str, *, item_id: str, asset_type: str, prompt: str | None = None,
                      asset_uri: str | None = None, view_name: str | None = None, metadata: dict[str, Any] | None = None,
                      status: str = "pending_generation", source: str = "life_collection") -> dict[str, Any]:
    asset_id = new_id("asset")
    conn.execute(
        """INSERT INTO collection_item_assets(id, owner_kind, owner_id, item_id, asset_type, view_name, asset_uri, prompt_text, metadata_json, status)
             VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (asset_id, owner_kind, owner_id, item_id, asset_type, view_name or asset_type, asset_uri, prompt, dumps(metadata or {}), status),
    )
    append_journal(conn, owner_kind, owner_id, "collection_item_asset_created", {"asset_id": asset_id, "item_id": item_id, "asset_type": asset_type, "status": status}, source)
    return _row_to_asset(conn.execute("SELECT * FROM collection_item_assets WHERE id=?", (asset_id,)).fetchone())


def list_item_assets(conn, owner_kind: str, owner_id: str, *, item_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT * FROM collection_item_assets WHERE owner_kind=? AND owner_id=? AND item_id=?"
    params: list[Any] = [owner_kind, owner_id, item_id]
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at LIMIT ?"; params.append(int(limit))
    return [_row_to_asset(r) for r in conn.execute(sql, params).fetchall()]


def generate_assets(conn, owner_kind: str, owner_id: str, *, item_id: str, source: str = "life_collection") -> dict[str, Any]:
    item = get_collection_item(conn, owner_kind, owner_id, item_id)
    collection = get_collection(conn, owner_kind, owner_id, collection_id=item["collection_id"])
    existing = list_item_assets(conn, owner_kind, owner_id, item_id=item_id)
    have_types = {a["asset_type"] for a in existing}
    created = []
    for view in _asset_requirements_for_collection(collection):
        if view in have_types:
            continue
        created.append(create_item_asset(conn, owner_kind, owner_id, item_id=item_id, asset_type=view, prompt=build_asset_generation_prompt(collection, item, view=view), status="pending_generation", source=source))
    return {"ok": True, "item": get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True), "created_assets": created, "rendered": render_item_assets(get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True))}


def set_item_asset_uri(conn, owner_kind: str, owner_id: str, *, asset_id: str, asset_uri: str, status: str = "available", metadata: dict[str, Any] | None = None, source: str = "life_collection") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM collection_item_assets WHERE id=? AND owner_kind=? AND owner_id=?", (asset_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise CollectionError(f"asset not found: {asset_id}")
    old = _row_to_asset(row)
    meta = old.get("metadata") or {}
    if metadata:
        meta.update(metadata)
    conn.execute("UPDATE collection_item_assets SET asset_uri=?, status=?, metadata_json=?, updated_at=datetime('now') WHERE id=?", (asset_uri, status, dumps(meta), asset_id))
    append_journal(conn, owner_kind, owner_id, "collection_item_asset_updated", {"asset_id": asset_id, "asset_uri": asset_uri, "status": status}, source)
    return _row_to_asset(conn.execute("SELECT * FROM collection_item_assets WHERE id=?", (asset_id,)).fetchone())


def check_out_item(conn, owner_kind: str, owner_id: str, *, item_id: str, reason: str = "checkout", event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    item = update_collection_item(conn, owner_kind, owner_id, item_id=item_id, availability_state="in_use", usage_state={"checked_out_at": now_iso(), "reason": reason, "event_id": event_id}, source=source)
    usage_id = new_id("coluse")
    conn.execute("INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)", (usage_id, owner_kind, owner_id, item_id, "checkout", event_id, reason, "done"))
    return {"ok": True, "item": item, "usage_id": usage_id}


def return_item(conn, owner_kind: str, owner_id: str, *, item_id: str, cleanliness_state: str = "dirty", reason: str = "return", event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    item = update_collection_item(conn, owner_kind, owner_id, item_id=item_id, availability_state="available", cleanliness_state=cleanliness_state, usage_state={"returned_at": now_iso(), "reason": reason, "event_id": event_id}, source=source)
    usage_id = new_id("coluse")
    conn.execute("INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)", (usage_id, owner_kind, owner_id, item_id, "return", event_id, reason, "done"))
    return {"ok": True, "item": item, "usage_id": usage_id}


def consume_item(conn, owner_kind: str, owner_id: str, *, item_id: str, quantity: float = 1, reason: str = "consume", event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    """Consume (use up) quantity of an item. Item quantity decreases.

    NOTE: consume never triggers asset re-generation. Assets are created once
    at item creation time and persist regardless of quantity changes.
    """
    quantity = float(quantity)
    if quantity <= 0:
        raise CollectionError("consume quantity must be positive")
    item = get_collection_item(conn, owner_kind, owner_id, item_id)
    current = float(item.get("quantity") or 0)
    if current < quantity:
        raise CollectionError(f"insufficient quantity: has {current}, tried to consume {quantity}")
    new_qty = current - quantity
    conn.execute(
        "UPDATE collection_items SET quantity=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (new_qty, item_id, owner_kind, owner_id),
    )
    usage_id = new_id("coluse")
    conn.execute(
        "INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)",
        (usage_id, owner_kind, owner_id, item_id, "consume", event_id, f"{reason} (qty -{quantity})", "done"),
    )
    append_journal(conn, owner_kind, owner_id, "collection_item_consumed", {"item_id": item_id, "consumed": quantity, "remaining": new_qty, "reason": reason}, source)
    updated = get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=False)
    return {"ok": True, "item": updated, "consumed": quantity, "remaining": new_qty, "usage_id": usage_id}


def restock_item(conn, owner_kind: str, owner_id: str, *, item_id: str, quantity: float = 1, reason: str = "restock", event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    """Restock (add) quantity to an item. Item quantity increases.

    NOTE: restock never triggers asset re-generation. Assets are created once
    at item creation time and persist regardless of quantity changes.
    """
    quantity = float(quantity)
    if quantity <= 0:
        raise CollectionError("restock quantity must be positive")
    item = get_collection_item(conn, owner_kind, owner_id, item_id)
    current = float(item.get("quantity") or 0)
    new_qty = current + quantity
    conn.execute(
        "UPDATE collection_items SET quantity=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (new_qty, item_id, owner_kind, owner_id),
    )
    usage_id = new_id("coluse")
    conn.execute(
        "INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)",
        (usage_id, owner_kind, owner_id, item_id, "restock", event_id, f"{reason} (qty +{quantity})", "done"),
    )
    append_journal(conn, owner_kind, owner_id, "collection_item_restocked", {"item_id": item_id, "restocked": quantity, "total": new_qty, "reason": reason}, source)
    updated = get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=False)
    return {"ok": True, "item": updated, "restocked": quantity, "total": new_qty, "usage_id": usage_id}


# ---------------------------------------------------------------------------
# Loadout / Backpack: pack items from collection onto body/backpack and back.
# ---------------------------------------------------------------------------

def pack_item(conn, owner_kind: str, owner_id: str, *, item_id: str, quantity: float = 1,
              slot: str = "backpack", reason: str = "pack", event_id: str | None = None,
              source: str = "life_collection") -> dict[str, Any]:
    """Pack item(s) from collection storage onto body or backpack.

    - For quantity-managed items (quantity > 1): splits — collection qty decreases, loadout qty increases.
    - For unique items (quantity == 1): marks item as in_use, creates loadout entry.
    - slot: 'backpack' (carried) or 'worn' (equipped on body as clothing).
    """
    quantity = float(quantity)
    if quantity <= 0:
        raise CollectionError("pack quantity must be positive")
    item = get_collection_item(conn, owner_kind, owner_id, item_id)
    collection_id = item.get("collection_id")
    # Look up collection_type
    col_row = conn.execute("SELECT collection_type FROM item_collections WHERE id=?", (collection_id,)).fetchone()
    collection_type = col_row["collection_type"] if col_row else "custom"
    current_qty = float(item.get("quantity") or 0)

    if current_qty < quantity:
        raise CollectionError(f"insufficient quantity to pack: has {current_qty}, tried to pack {quantity}")

    is_unique = current_qty <= 1
    if is_unique:
        # Unique item: mark as in_use
        update_collection_item(conn, owner_kind, owner_id, item_id=item_id,
                               availability_state="in_use",
                               usage_state={"packed_at": now_iso(), "reason": reason, "event_id": event_id},
                               source=source)
    else:
        # Quantity-managed: reduce collection quantity
        new_qty = current_qty - quantity
        conn.execute(
            "UPDATE collection_items SET quantity=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
            (new_qty, item_id, owner_kind, owner_id),
        )

    loadout_id = new_id("loadout")
    conn.execute(
        """INSERT INTO agent_loadout(id, owner_kind, owner_id, item_id, collection_id, collection_type, name, slot, quantity, reason, event_id, status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (loadout_id, owner_kind, owner_id, item_id, collection_id, collection_type,
         item.get("name"), slot, quantity, reason, event_id, "active"),
    )
    usage_id = new_id("coluse")
    conn.execute(
        "INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)",
        (usage_id, owner_kind, owner_id, item_id, "pack", event_id, f"{reason} (qty {quantity}, slot={slot})", "done"),
    )
    append_journal(conn, owner_kind, owner_id, "item_packed", {
        "item_id": item_id, "loadout_id": loadout_id, "quantity": quantity, "slot": slot, "reason": reason,
        "collection_remaining": (current_qty - quantity) if not is_unique else 0,
    }, source)
    return {"ok": True, "loadout_id": loadout_id, "item_id": item_id, "packed": quantity, "slot": slot,
            "collection_remaining": (current_qty - quantity) if not is_unique else 0}


def unpack_item(conn, owner_kind: str, owner_id: str, *, loadout_id: str | None = None,
                item_id: str | None = None, quantity: float | None = None,
                reason: str = "unpack", source: str = "life_collection") -> dict[str, Any]:
    """Return item(s) from backpack/on-body back to collection storage.

    - If loadout_id given, returns that specific entry (full or partial quantity).
    - If item_id given without loadout_id, finds active loadout entry for that item.
    - quantity=None means return all.
    """
    # Find the loadout entry
    if loadout_id:
        row = conn.execute(
            "SELECT * FROM agent_loadout WHERE id=? AND owner_kind=? AND owner_id=? AND status='active'",
            (loadout_id, owner_kind, owner_id),
        ).fetchone()
    elif item_id:
        row = conn.execute(
            "SELECT * FROM agent_loadout WHERE item_id=? AND owner_kind=? AND owner_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
            (item_id, owner_kind, owner_id),
        ).fetchone()
    else:
        raise CollectionError("loadout_id or item_id is required")
    if not row:
        raise CollectionError(f"no active loadout entry found")

    packed_qty = float(row["quantity"])
    return_qty = float(quantity) if quantity is not None else packed_qty
    if return_qty <= 0 or return_qty > packed_qty:
        raise CollectionError(f"invalid return quantity: {return_qty}, packed: {packed_qty}")

    item = get_collection_item(conn, owner_kind, owner_id, row["item_id"])
    current_qty = float(item.get("quantity") or 0)

    # Was this a unique item (originally qty 1, marked in_use)?
    was_unique = item.get("availability_state") == "in_use" and return_qty >= packed_qty

    if was_unique:
        # Return unique item to available
        update_collection_item(conn, owner_kind, owner_id, item_id=row["item_id"],
                               availability_state="available",
                               usage_state={"unpacked_at": now_iso(), "reason": reason},
                               source=source)
    else:
        # Return quantity to collection
        new_qty = current_qty + return_qty
        conn.execute(
            "UPDATE collection_items SET quantity=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
            (new_qty, row["item_id"], owner_kind, owner_id),
        )

    # Update or close loadout entry
    remaining = packed_qty - return_qty
    if remaining <= 0:
        conn.execute("UPDATE agent_loadout SET status='returned', updated_at=datetime('now') WHERE id=?", (row["id"],))
    else:
        conn.execute("UPDATE agent_loadout SET quantity=?, updated_at=datetime('now') WHERE id=?", (remaining, row["id"]))

    usage_id = new_id("coluse")
    conn.execute(
        "INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)",
        (usage_id, owner_kind, owner_id, row["item_id"], "unpack", row["event_id"], f"{reason} (qty {return_qty})", "done"),
    )
    append_journal(conn, owner_kind, owner_id, "item_unpacked", {
        "loadout_id": row["id"], "item_id": row["item_id"], "returned": return_qty, "remaining_in_loadout": remaining,
    }, source)
    return {"ok": True, "loadout_id": row["id"], "item_id": row["item_id"], "returned": return_qty,
            "remaining_in_loadout": remaining, "collection_total": current_qty + return_qty}


def get_loadout(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Get current on-body + backpack loadout."""
    rows = conn.execute(
        "SELECT * FROM agent_loadout WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY slot, created_at",
        (owner_kind, owner_id),
    ).fetchall()
    worn = []
    backpack = []
    for r in rows:
        entry = dict(r)
        if entry.get("slot") == "worn":
            worn.append(entry)
        else:
            backpack.append(entry)
    return {"worn": worn, "backpack": backpack, "total_items": len(worn) + len(backpack)}


def maintain_item(conn, owner_kind: str, owner_id: str, *, item_id: str, maintenance_type: str = "clean", reason: str = "maintenance", source: str = "life_collection") -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if maintenance_type in {"clean", "wash", "laundry"}:
        fields["cleanliness_state"] = "clean"
    if maintenance_type in {"repair", "fix"}:
        fields["condition_score"] = 100
    if maintenance_type in {"air", "airing"}:
        fields["cleanliness_state"] = "airing"
    item = update_collection_item(conn, owner_kind, owner_id, item_id=item_id, source=source, **fields)
    run_id = new_id("colmaint")
    conn.execute("INSERT INTO collection_maintenance_runs(id, owner_kind, owner_id, item_id, maintenance_type, status, result_json) VALUES(?,?,?,?,?,?,?)", (run_id, owner_kind, owner_id, item_id, maintenance_type, "completed", dumps({"reason": reason, "item": item})))
    append_journal(conn, owner_kind, owner_id, "collection_item_maintained", {"item_id": item_id, "maintenance_type": maintenance_type, "run_id": run_id}, source)
    return {"ok": True, "maintenance_run_id": run_id, "item": item}


def build_outfit(conn, owner_kind: str, owner_id: str, *, occasion: str = "daily", weather: str | None = None, mood: str | None = None,
                 event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    ensure_default_collections(conn, owner_kind, owner_id)
    picks: dict[str, Any] = {}
    for ctype in ["wardrobe", "shoe_cabinet", "sock_drawer", "accessory_cabinet", "vanity"]:
        items = list_collection_items(conn, owner_kind, owner_id, collection_type=ctype, availability_state="available", limit=20)
        usable = [i for i in items if i.get("cleanliness_state") not in {"dirty", "laundry", "repair_needed"} and i.get("status") == "active"]
        picks[ctype] = usable[0] if usable else None
    item_ids = [p["id"] for p in picks.values() if p]
    plan_id = new_id("outfit")
    conn.execute(
        """INSERT INTO outfit_plans(id, owner_kind, owner_id, occasion, event_id, item_ids_json, context_json, reasoning_json, status)
             VALUES(?,?,?,?,?,?,?,?,?)""",
        (plan_id, owner_kind, owner_id, occasion, event_id, dumps(item_ids), dumps({"weather": weather, "mood": mood}), dumps({"rule": "first available clean items by collection"}), "draft"),
    )
    append_journal(conn, owner_kind, owner_id, "outfit_plan_created", {"outfit_plan_id": plan_id, "item_ids": item_ids, "occasion": occasion}, source)
    plan = get_outfit_plan(conn, owner_kind, owner_id, plan_id)
    return {"ok": True, "outfit_plan": plan, "picks": picks, "rendered": render_outfit(plan, picks)}


def get_outfit_plan(conn, owner_kind: str, owner_id: str, outfit_plan_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM outfit_plans WHERE id=? AND owner_kind=? AND owner_id=?", (outfit_plan_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise CollectionError(f"outfit plan not found: {outfit_plan_id}")
    return _row_to_outfit(row)


def list_outfit_plans(conn, owner_kind: str, owner_id: str, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = "SELECT * FROM outfit_plans WHERE owner_kind=? AND owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
    return [_row_to_outfit(r) for r in conn.execute(sql, params).fetchall()]


def render_collections_summary(collections: list[dict[str, Any]]) -> str:
    lines = ["物品集合", "========"]
    if not collections:
        return "物品集合\n========\n还没有集合。可用 /life closet init 创建默认衣橱/鞋柜/梳妆台/配饰柜/袜子抽屉。"
    for c in collections:
        lines.append(f"- {c['name']} ({c['collection_type']}) · {c['status']} · {c.get('description') or ''}")
    return "\n".join(lines)


def render_items(title: str, items: list[dict[str, Any]]) -> str:
    lines = [title, "=" * max(4, len(title))]
    if not items:
        lines.append("暂无条目。")
    for i, item in enumerate(items, 1):
        material = item.get("material_spec") or {}
        mat = material.get("material") or material.get("fabric") or "未注明"
        lines.append(f"{i}. {item['name']}（{item.get('cleanliness_state')} / {item.get('availability_state')}）")
        lines.append(f"   类型：{item.get('item_type')}；数量：{item.get('quantity')}；状态：{item.get('status')}；材质：{mat}")
        if not get_display_image(item):
            lines.append("   图像：待生成（display_image 和 reference_image 均未设置）。")
    return "\n".join(lines)


def render_item_assets(item: dict[str, Any]) -> str:
    lines = [f"{item.get('name')} · 资产图", "================"]
    bundle = item.get("asset_bundle") or {}
    disp = bundle.get("display_image")
    ref = bundle.get("reference_image")
    lines.append(f"- display_image（封面/展示）：{disp or '（未设置，回退 reference）'}")
    lines.append(f"- reference_image（参考/sheet）：{ref or '（未设置，回退 display）'}")
    status = bundle.get("status", "unknown")
    lines.append(f"- 状态：{status}")
    # Legacy per-view assets (if any from old data)
    assets = item.get("assets") or []
    if assets:
        lines.append("")
        lines.append("旧版 per-view 资产（仅参考）：")
        for a in assets:
            lines.append(f"  - {a.get('asset_type')} · {a.get('status')}" + (f" → {a.get('asset_uri')}" if a.get("asset_uri") else " · 待生成"))
    return "\n".join(lines)


def render_outfit(plan: dict[str, Any], picks: dict[str, Any]) -> str:
    names = {
        "wardrobe": "衣服",
        "shoe_cabinet": "鞋",
        "sock_drawer": "袜子",
        "accessory_cabinet": "配饰",
        "vanity": "梳妆",
    }
    lines = ["今日穿搭 / 造型草案", "================"]
    for key, label in names.items():
        item = picks.get(key)
        lines.append(f"{label}：{item['name'] if item else '暂无可用条目'}")
    lines.append(f"场合：{plan.get('occasion')}")
    lines.append("说明：只从已入库集合中选择；缺失项不会凭空生成，会提示补充或清洗/维护。")
    return "\n".join(lines)
