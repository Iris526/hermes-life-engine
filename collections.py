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
            "subject": "clothing_item_only",
            "views": ["front_view", "side_view", "back_view", "flat_lay_optional", "material_sheet"],
            "must": ["只画衣服本体，不画穿在人身上", "简洁背景", "明确版型、长度、材质、颜色"],
            "exclude": ["full_body_worn_by_person", "model_pose"],
        },
        "usage_rule": {
            "checkout_for": ["outfit", "sleepwear", "work", "travel", "daily_life"],
            "return_states": ["clean", "dirty", "airing", "repair_needed"],
            "cannot_use_when": ["dirty", "repair_needed", "archived"],
            "requires_visual_reference_on_use": True,
            "prohibit_text_only_reconstruction": True,
            "lazy_generate_missing_assets": True,
        },
        "required_metadata": ["category", "color_family", "season", "style_tags", "material", "warmth", "formalness"],
    },
    "shoe_cabinet": {
        "name": "鞋柜",
        "description": "鞋子本体资产集合：靴子、日常鞋、运动鞋、室内鞋、雨鞋等。",
        "entry_image_rule": {
            "subject": "shoe_pair_only",
            "views": ["side_view", "top_view", "back_view", "sole_view", "material_sheet"],
            "must": ["只画鞋子本体，不画穿在脚上", "明确鞋型、鞋底、鞋跟、材质与天气适配"],
            "exclude": ["worn_on_feet", "full_body_model"],
        },
        "usage_rule": {
            "checkout_for": ["outfit", "outdoor", "indoor", "rain"],
            "return_states": ["clean", "dirty", "airing", "repair_needed"],
            "weather_filter": True,
            "requires_visual_reference_on_use": True,
            "prohibit_text_only_reconstruction": True,
            "lazy_generate_missing_assets": True,
        },
        "required_metadata": ["shoe_type", "color_family", "weather_suitability", "material", "comfort", "season"],
    },
    "sock_drawer": {
        "name": "袜子抽屉",
        "description": "袜子集合：短袜、长袜、连裤袜、保暖袜、运动袜、居家袜。",
        "entry_image_rule": {
            "subject": "socks_only_flat_lay",
            "views": ["front_flat_view", "back_flat_view", "material_thickness_sheet"],
            "must": ["不画穿在脚上", "重点展示长度、图案、厚薄、材质"],
            "exclude": ["worn_on_feet"],
        },
        "usage_rule": {"quantity_managed": True, "return_states": ["laundry", "clean", "worn_out"], "requires_visual_reference_on_use": True, "prohibit_text_only_reconstruction": True, "lazy_generate_missing_assets": True},
        "required_metadata": ["sock_type", "length", "thickness", "material", "color_family", "quantity_per_pair"],
    },
    "accessory_cabinet": {
        "name": "配饰柜",
        "description": "配饰集合：发饰、项链、耳饰、手链、腰带、包、披肩、护符、铜铃等。",
        "entry_image_rule": {
            "subject": "accessory_item_only",
            "views": ["main_display", "front_or_top_view", "side_or_detail_view", "material_sheet", "detail_views_optional"],
            "must": ["单品为主", "展示材质、吊坠/纹样/扣具细节"],
            "exclude": ["default_worn_on_body"],
        },
        "usage_rule": {"stackable": True, "checkout_for": ["outfit", "ritual", "identity", "work"], "requires_visual_reference_on_use": True, "prohibit_text_only_reconstruction": True, "lazy_generate_missing_assets": True},
        "required_metadata": ["accessory_type", "material", "color_family", "style_tags", "symbolic_meaning"],
    },
    "vanity": {
        "name": "梳妆台",
        "description": "妆容、发型、护肤/整理工具与可复用造型方案。",
        "entry_image_rule": {
            "subject": "makeup_or_hairstyle_sheet",
            "views": ["front_view", "side_view_optional", "back_view_for_hairstyle", "detail_sheet", "palette_or_material_notes"],
            "must": ["妆容可以用 face chart；发型必须展示正侧背", "不强制完整穿搭图"],
            "exclude": ["unrelated_outfit_full_body"],
        },
        "usage_rule": {"recipe_allowed": True, "checkout_for": ["makeup", "hairstyle", "daily_grooming", "occasion"], "requires_visual_reference_on_use": True, "prohibit_text_only_reconstruction": True, "lazy_generate_missing_assets": True},
        "required_metadata": ["vanity_type", "style_tags", "palette", "hair_accessories", "time_cost_minutes"],
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
    rule = collection.get("image_generation_rule") or {}
    must = "; ".join(rule.get("must") or [])
    exclude = "; ".join(rule.get("exclude") or [])
    material = item.get("material_spec") or {}
    attrs = item.get("attributes") or {}
    view_text = f" View: {view}." if view else ""
    return (
        f"Create an inventory asset sheet for {collection.get('name')} / {collection.get('collection_type')}: {item.get('name')}."
        f" Subject rule: {rule.get('subject', 'item only')}.{view_text} "
        f"Description: {item.get('description') or ''}. Attributes: {attrs}. Material: {material}. "
        f"Must: {must}. Exclude: {exclude}. No full-body worn styling unless collection rule explicitly asks for a sheet."
    )


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
        "status": "needs_generation",
        "requirements": _asset_requirements_for_collection(collection),
        "primary_image": None,
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
    # Create pending asset jobs for every required view. Actual image generation is fulfilled by image pipeline.
    for view in asset_bundle["requirements"]:
        prompt = build_asset_generation_prompt(collection, item, view=view)
        create_item_asset(conn, owner_kind, owner_id, item_id=item_id, asset_type=view, prompt=prompt, status="pending_generation", source=source)
    append_journal(conn, owner_kind, owner_id, "collection_item_created", {"item_id": item_id, "collection_id": collection["id"], "name": name, "asset_requirements": asset_bundle["requirements"]}, source)
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


def available_item_assets(conn, owner_kind: str, owner_id: str, *, item_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return fulfilled image references for an item."""
    return [
        a for a in list_item_assets(conn, owner_kind, owner_id, item_id=item_id, status="available", limit=limit)
        if a.get("asset_uri")
    ]


def ensure_visual_references(conn, owner_kind: str, owner_id: str, *, item_id: str, lazy_generate: bool = True,
                             source: str = "life_collection") -> dict[str, Any]:
    """Guard against text-only reconstruction; lazily create missing asset jobs."""
    item = get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True)
    refs = available_item_assets(conn, owner_kind, owner_id, item_id=item_id)
    if refs:
        return {"usable": True, "item": item, "references": refs, "created_assets": [], "reason": "available visual references found"}
    created: list[dict[str, Any]] = []
    if lazy_generate:
        created = generate_assets(conn, owner_kind, owner_id, item_id=item_id, source=source).get("created_assets", [])
        item = get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True)
    return {
        "usable": False,
        "item": item,
        "references": [],
        "created_assets": created,
        "reason": "missing available visual references; lazy asset jobs created" if created else "missing available visual references; pending asset jobs already exist",
    }


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
    if asset_uri and status == "available":
        item = get_collection_item(conn, owner_kind, owner_id, old["item_id"])
        bundle = dict(item.get("asset_bundle") or {})
        if not bundle.get("primary_image"):
            bundle["primary_image"] = asset_uri
        bundle["status"] = "available"
        refs = list(bundle.get("reference_assets") or [])
        ref = {"asset_id": asset_id, "asset_type": old.get("asset_type"), "view_name": old.get("view_name"), "asset_uri": asset_uri}
        if all(r.get("asset_id") != asset_id for r in refs if isinstance(r, dict)):
            refs.append(ref)
        bundle["reference_assets"] = refs
        update_collection_item(conn, owner_kind, owner_id, item_id=old["item_id"], asset_bundle=bundle, source=source)
    append_journal(conn, owner_kind, owner_id, "collection_item_asset_updated", {"asset_id": asset_id, "asset_uri": asset_uri, "status": status}, source)
    return _row_to_asset(conn.execute("SELECT * FROM collection_item_assets WHERE id=?", (asset_id,)).fetchone())


def check_out_item(conn, owner_kind: str, owner_id: str, *, item_id: str, reason: str = "checkout", event_id: str | None = None,
                   require_visual_reference: bool = True, source: str = "life_collection") -> dict[str, Any]:
    visual = ensure_visual_references(conn, owner_kind, owner_id, item_id=item_id, lazy_generate=True, source=source) if require_visual_reference else {"usable": True, "references": []}
    if require_visual_reference and not visual.get("usable"):
        return {
            "ok": False,
            "error": "visual_reference_required",
            "item": visual.get("item"),
            "created_assets": visual.get("created_assets", []),
            "rendered": render_visual_reference_required(visual.get("item") or {}, visual.get("created_assets", [])),
        }
    item = update_collection_item(conn, owner_kind, owner_id, item_id=item_id, availability_state="in_use", usage_state={"checked_out_at": now_iso(), "reason": reason, "event_id": event_id, "reference_assets": visual.get("references", [])}, source=source)
    usage_id = new_id("coluse")
    conn.execute("INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)", (usage_id, owner_kind, owner_id, item_id, "checkout", event_id, reason, "done"))
    return {"ok": True, "item": item, "usage_id": usage_id, "reference_assets": visual.get("references", [])}


def return_item(conn, owner_kind: str, owner_id: str, *, item_id: str, cleanliness_state: str = "dirty", reason: str = "return", event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    item = update_collection_item(conn, owner_kind, owner_id, item_id=item_id, availability_state="available", cleanliness_state=cleanliness_state, usage_state={"returned_at": now_iso(), "reason": reason, "event_id": event_id}, source=source)
    usage_id = new_id("coluse")
    conn.execute("INSERT INTO collection_usage_history(id, owner_kind, owner_id, item_id, operation, event_id, reason, status) VALUES(?,?,?,?,?,?,?,?)", (usage_id, owner_kind, owner_id, item_id, "return", event_id, reason, "done"))
    return {"ok": True, "item": item, "usage_id": usage_id}


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
                 event_id: str | None = None, lazy_generate: bool = True, source: str = "life_collection") -> dict[str, Any]:
    ensure_default_collections(conn, owner_kind, owner_id)
    picks: dict[str, Any] = {}
    reference_assets: dict[str, list[dict[str, Any]]] = {}
    missing_visual_references: list[dict[str, Any]] = []
    for ctype in ["wardrobe", "shoe_cabinet", "sock_drawer", "accessory_cabinet", "vanity"]:
        items = list_collection_items(conn, owner_kind, owner_id, collection_type=ctype, availability_state="available", limit=20)
        usable = [i for i in items if i.get("cleanliness_state") not in {"dirty", "laundry", "repair_needed"} and i.get("status") == "active"]
        picks[ctype] = None
        for item in usable:
            visual = ensure_visual_references(conn, owner_kind, owner_id, item_id=item["id"], lazy_generate=lazy_generate, source=source)
            if visual.get("usable"):
                picks[ctype] = visual["item"]
                reference_assets[ctype] = visual.get("references", [])
                break
            missing_visual_references.append({"collection_type": ctype, "item_id": item["id"], "name": item.get("name"), "created_assets": visual.get("created_assets", []), "reason": visual.get("reason")})
    item_ids = [p["id"] for p in picks.values() if p]
    status = "draft" if item_ids else "waiting_assets"
    reasoning = {
        "rule": "select clean available items only when available image asset_uri references exist; never reconstruct clothing from text only",
        "reference_assets": reference_assets,
        "missing_visual_references": missing_visual_references,
        "lazy_generate_missing_assets": lazy_generate,
    }
    plan_id = new_id("outfit")
    conn.execute(
        """INSERT INTO outfit_plans(id, owner_kind, owner_id, occasion, event_id, item_ids_json, context_json, reasoning_json, status)
             VALUES(?,?,?,?,?,?,?,?,?)""",
        (plan_id, owner_kind, owner_id, occasion, event_id, dumps(item_ids), dumps({"weather": weather, "mood": mood}), dumps(reasoning), status),
    )
    append_journal(conn, owner_kind, owner_id, "outfit_plan_created", {"outfit_plan_id": plan_id, "item_ids": item_ids, "occasion": occasion, "reference_assets": reference_assets, "missing_visual_references": missing_visual_references}, source)
    plan = get_outfit_plan(conn, owner_kind, owner_id, plan_id)
    ok = bool(item_ids) or not missing_visual_references
    return {"ok": ok, "outfit_plan": plan, "picks": picks, "reference_assets": reference_assets, "missing_visual_references": missing_visual_references, "rendered": render_outfit(plan, picks)}

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
        if (item.get("asset_bundle") or {}).get("status") == "needs_generation":
            lines.append("   图像：待生成资产图（按集合三视图/材质规则）。")
    return "\n".join(lines)


def render_item_assets(item: dict[str, Any]) -> str:
    lines = [f"{item.get('name')} · 资产图", "================"]
    assets = item.get("assets") or []
    if not assets:
        lines.append("暂无资产图任务。")
    for a in assets:
        lines.append(f"- {a.get('asset_type')} · {a.get('status')}")
        if a.get("asset_uri"):
            lines.append(f"  文件：{a.get('asset_uri')}")
        else:
            lines.append("  待生成。")
            if a.get("prompt_text"):
                lines.append(f"  提示词：{a.get('prompt_text')[:240]}...")
    return "\n".join(lines)


def render_visual_reference_required(item: dict[str, Any], created_assets: list[dict[str, Any]] | None = None) -> str:
    lines = ["需要资产图引用", "=============="]
    lines.append(f"{item.get('name', '该物品')} 还没有可用的图片引用，不能只按文字描述使用。")
    if created_assets:
        lines.append("已按集合规则懒生成待处理资产任务：")
        for a in created_assets:
            lines.append(f"- {a.get('asset_type')} · {a.get('status')} · id={a.get('id')}")
    else:
        lines.append("已有待生成资产任务，请先生成并绑定 asset_uri。")
    return "\n".join(lines)


def render_outfit(plan: dict[str, Any], picks: dict[str, Any]) -> str:
    names = {
        "wardrobe": "衣服",
        "shoe_cabinet": "鞋",
        "sock_drawer": "袜子",
        "accessory_cabinet": "配饰",
        "vanity": "梳妆",
    }
    reasoning = plan.get("reasoning") or {}
    refs = reasoning.get("reference_assets") or {}
    missing = reasoning.get("missing_visual_references") or []
    lines = ["今日穿搭 / 造型草案", "================"]
    for key, label in names.items():
        item = picks.get(key)
        if item:
            uri = None
            ref_list = refs.get(key) or []
            if ref_list:
                uri = ref_list[0].get("asset_uri")
            lines.append(f"{label}：{item['name']}" + (f" · reference: {uri}" if uri else " · reference: 已记录"))
        else:
            lines.append(f"{label}：暂无可用条目/缺少资产图引用")
    lines.append(f"场合：{plan.get('occasion')}；状态：{plan.get('status')}")
    if missing:
        lines.append("\n缺少图片引用，已按规则懒生成/复用资产任务；这些物品暂不能只靠文字描述用于出图：")
        for m in missing:
            created = m.get("created_assets") or []
            job_text = "，".join([f"{a.get('asset_type')}:{a.get('id')}" for a in created]) or "已有 pending 任务"
            lines.append(f"- {m.get('name')}（{m.get('collection_type')}）：{job_text}")
    lines.append("说明：只从已入库集合中选择；使用/出图必须引用可用 asset_uri，禁止只按文字描述重建物品。")
    return "\n".join(lines)

