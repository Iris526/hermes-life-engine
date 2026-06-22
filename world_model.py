"""世界本体模型：地图、地点、背景条目和势力影响。

本模块承载 LifeEngine 的结构化世界观底座。文本设定仍保存在
summary/content/background_text 中，但生效边界由 profile、region、place、
lore、faction_presence 等结构化记录控制，供事件、日程、社交投影、上下文和
WebUI 引用，而不是依赖提示词里临时承诺。
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id

WORLD_SCOPE_ID = "__world__"

_SCOPE_KINDS = {"world", "region", "place"}

_DEFAULT_MAP_CANVAS = {"width": 100, "height": 100, "unit": "grid", "projection": "local_grid"}
_BUILDING_PLACE_TYPES = {"building", "home", "shop", "market", "shrine", "temple", "gate", "venue", "station"}
_TERRAIN_LABELS = {
    "urban": "城区",
    "urban_ruins": "城区废墟",
    "street": "街巷",
    "market": "集市",
    "wasteland": "荒原",
    "water": "水域",
    "forest": "林地",
    "mountain": "山地",
    "plain": "平原",
    "custom": "地形",
}

def _row(row) -> dict[str, Any]:
    """把 SQLite row 安全转成普通 dict。"""
    return dict(row) if row else {}


def _decode_json_fields(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """把指定 JSON 字段解码为公开字段名。"""
    for field in fields:
        if field in item:
            item[field[:-5] if field.endswith("_json") else field] = loads(item.pop(field), {})
    return item


def _decode_profile(row) -> dict[str, Any]:
    """解码世界档案行。"""
    return _decode_json_fields(_row(row), ["rules_json", "evidence_json"])


def _decode_region(row) -> dict[str, Any]:
    """解码区域行。"""
    return _decode_json_fields(_row(row), ["traits_json", "evidence_json"])


def _decode_place(row) -> dict[str, Any]:
    """解码地点行。"""
    return _decode_json_fields(_row(row), ["coordinates_json", "traits_json", "evidence_json"])


def _decode_lore(row) -> dict[str, Any]:
    """解码知识条目行。"""
    item = _row(row)
    if "tags_json" in item:
        item["tags"] = loads(item.pop("tags_json"), [])
    if "evidence_json" in item:
        item["evidence"] = loads(item.pop("evidence_json"), {})
    return item


def _decode_presence(row) -> dict[str, Any]:
    """解码势力影响行。"""
    return _decode_json_fields(_row(row), ["evidence_json"])


def _clean_key(value: Any, fallback: str | None = None) -> str:
    """规整外部传入的稳定 key。"""
    key = str(value or fallback or "").strip()
    if not key:
        raise ValueError("world key is required")
    return key


def _as_float(value: Any, default: float) -> float:
    """把地图坐标字段规整成浮点数。

    输入来自 rules/traits/coordinates 中的用户可编辑字段；输出用于地图派生结构。
    调用方是 map_state 的内部归一化流程。非数字值回落到 default，避免坏坐标让
    整个世界地图不可读。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp_map_value(value: Any, default: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    """限制地图坐标/尺寸范围。

    输入是外部可编辑坐标；输出是画布范围内的数值。调用方是地图 region/place
    派生逻辑。这里不抛错，保持旧数据兼容；严格校验留给更高层工具或测试。
    """
    return max(minimum, min(maximum, _as_float(value, default)))


def _nested_map_config(value: dict[str, Any] | None) -> dict[str, Any]:
    """读取记录中的 map 子结构。

    输入通常是 profile.rules、region.traits 或 place.coordinates；输出是可编辑
    地图配置字典。调用方只读返回值，不修改原对象，避免 reader/WebUI 间共享状态
    被意外污染。
    """
    if not isinstance(value, dict):
        return {}
    nested = value.get("map")
    return dict(nested) if isinstance(nested, dict) else {}


def _coord_value(data: dict[str, Any], key: str, default: float) -> float:
    """从多种兼容坐标字段中取值。

    输入是 coordinates/traits.map；输出是地图数值。支持 `x/y`、`map.x/map.y`、
    `position.x/position.y` 和 `grid_x/grid_y`，让旧数据和人工输入都能落到同一张
    地图上。
    """
    nested = _nested_map_config(data)
    position = data.get("position") if isinstance(data.get("position"), dict) else {}
    aliases = {
        "x": ["x", "grid_x", "lng", "longitude"],
        "y": ["y", "grid_y", "lat", "latitude"],
        "width": ["width", "w"],
        "height": ["height", "h"],
    }.get(key, [key])
    for source in (data, nested, position):
        for alias in aliases:
            if isinstance(source, dict) and source.get(alias) is not None:
                return _as_float(source.get(alias), default)
    return float(default)


def _terrain_from_record(record: dict[str, Any], fallback: str = "custom") -> str:
    """从 region/place 记录解析地形类型。

    输入是解码后的 region/place；输出是稳定 terrain key。地形可以来自 traits.terrain、
    traits.map.terrain、coordinates.terrain 或记录类型。调用方是 map_state 和 WebUI
    reader，用于给地图层上色和生成图例。
    """
    traits = record.get("traits") if isinstance(record.get("traits"), dict) else {}
    coordinates = record.get("coordinates") if isinstance(record.get("coordinates"), dict) else {}
    for source in (traits, _nested_map_config(traits), coordinates, _nested_map_config(coordinates)):
        if isinstance(source, dict) and source.get("terrain"):
            return str(source.get("terrain")).strip() or fallback
    type_key = str(record.get("region_type") or record.get("place_type") or "").strip()
    if type_key in {"street", "market"}:
        return type_key
    if type_key in {"city", "district", "gate"}:
        return "urban"
    return fallback


def _region_shape(region: dict[str, Any], index: int, total: int) -> dict[str, Any]:
    """把区域记录转换为地图地形块。

    输入是解码后的 region 及其列表序号；输出包含 x/y/width/height/terrain 的绘图
    结构。显式坐标来自 traits.map 或 traits；缺省时按序号生成稳定布局，保证
    WebUI 仍有实际地图可显示，同时通过 explicit_position 标明是否人工定位。
    """
    traits = region.get("traits") if isinstance(region.get("traits"), dict) else {}
    map_cfg = _nested_map_config(traits)
    explicit = any(key in traits or key in map_cfg for key in {"x", "y", "width", "height", "w", "h"})
    columns = max(1, min(3, total or 1))
    col = index % columns
    row = index // columns
    default_w = 88.0 / columns
    default_h = 28.0
    default_x = 6.0 + col * (default_w + 3.0)
    default_y = 8.0 + row * (default_h + 5.0)
    width = _clamp_map_value(_coord_value(traits, "width", default_w), default_w, 8.0, 96.0)
    height = _clamp_map_value(_coord_value(traits, "height", default_h), default_h, 8.0, 96.0)
    x = _clamp_map_value(_coord_value(traits, "x", default_x), default_x, 0.0, max(0.0, 100.0 - width))
    y = _clamp_map_value(_coord_value(traits, "y", default_y), default_y, 0.0, max(0.0, 100.0 - height))
    terrain = _terrain_from_record(region, "urban")
    return {
        "id": region.get("id"),
        "key": region.get("key"),
        "name": region.get("name") or region.get("key"),
        "region_type": region.get("region_type"),
        "terrain": terrain,
        "terrain_label": _TERRAIN_LABELS.get(terrain, terrain),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "explicit_position": explicit,
        "source": "region",
    }


def _marker_from_place(place: dict[str, Any], index: int,
                       region_shapes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把地点记录转换为地图标记。

    输入是解码后的 place 和所属区域形状；输出是可绘制 marker。坐标优先来自
    coordinates.x/y，其次从所属 region 的矩形内稳定派生。调用方是 map_state；
    副作用为无。
    """
    coordinates = place.get("coordinates") if isinstance(place.get("coordinates"), dict) else {}
    traits = place.get("traits") if isinstance(place.get("traits"), dict) else {}
    region = region_shapes.get(str(place.get("region_id") or "")) or {}
    explicit = any(key in coordinates or key in _nested_map_config(coordinates) for key in {"x", "y", "grid_x", "grid_y"})
    if explicit:
        x = _clamp_map_value(_coord_value(coordinates, "x", 50.0), 50.0)
        y = _clamp_map_value(_coord_value(coordinates, "y", 50.0), 50.0)
    elif region:
        slot = index % 9
        x = _clamp_map_value(region.get("x", 8) + 5 + (slot % 3) * max(5.0, float(region.get("width", 25)) / 3), 50.0)
        y = _clamp_map_value(region.get("y", 8) + 6 + (slot // 3) * max(5.0, float(region.get("height", 18)) / 3), 50.0)
    else:
        x = _clamp_map_value(12 + (index % 8) * 10, 50.0)
        y = _clamp_map_value(18 + (index // 8) * 12, 50.0)
    importance = _as_float(coordinates.get("importance", traits.get("importance", 50)), 50.0)
    place_type = str(place.get("place_type") or "place")
    is_building = place_type in _BUILDING_PLACE_TYPES or bool(traits.get("building_type") or coordinates.get("building_type"))
    is_important = bool(traits.get("important") or traits.get("landmark") or coordinates.get("important") or coordinates.get("landmark") or importance >= 70)
    marker_role = str(coordinates.get("marker_role") or traits.get("marker_role") or ("important_building" if is_building and is_important else "place"))
    terrain = _terrain_from_record(place, str(region.get("terrain") or "custom"))
    return {
        "id": place.get("id"),
        "key": place.get("key"),
        "name": place.get("name") or place.get("key"),
        "place_type": place_type,
        "region_id": place.get("region_id"),
        "region_name": place.get("region_name"),
        "terrain": terrain,
        "x": x,
        "y": y,
        "importance": importance,
        "is_building": is_building,
        "is_important": is_important,
        "marker_role": marker_role,
        "explicit_position": explicit,
        "source": "place",
    }


def _actor_marker(markers: list[dict[str, Any]], current_location: dict[str, Any] | None,
                  actor_label: str | None) -> dict[str, Any]:
    """根据当前事件 location 生成明灯位置标记。

    输入是地图 markers 和当前事件/状态 location；输出位于某个地点上的 actor 标记，
    或 status=unknown 的空标记。调用方是 map_state 和 WebUI reader。这里只使用
    结构化 world_place_id/key 或唯一名称匹配，不根据文本猜位置。
    """
    loc = current_location if isinstance(current_location, dict) else {}
    label = actor_label or "明灯"
    marker = None
    if loc.get("world_place_id"):
        marker = next((m for m in markers if m.get("id") == loc.get("world_place_id")), None)
    if not marker and loc.get("world_place_key"):
        marker = next((m for m in markers if m.get("key") == loc.get("world_place_key")), None)
    name = str(loc.get("name") or loc.get("display_name") or "").strip()
    if not marker and name:
        matched = [m for m in markers if m.get("name") == name or m.get("key") == name]
        marker = matched[0] if len(matched) == 1 else None
    if not marker:
        return {"label": label, "status": "unknown", "source": "current_location"}
    return {
        "label": label,
        "status": "located",
        "place_id": marker.get("id"),
        "place_key": marker.get("key"),
        "place_name": marker.get("name"),
        "x": marker.get("x"),
        "y": marker.get("y"),
        "source": "current_location",
    }


def map_state(profiles: list[dict[str, Any]], regions: list[dict[str, Any]], places: list[dict[str, Any]],
              *, current_location: dict[str, Any] | None = None,
              actor_label: str | None = "明灯") -> dict[str, Any]:
    """生成结构化世界地图。

    输入是已解码的世界档案、区域、地点，以及可选当前 location；输出是 WebUI 和
    context 可共享的地图对象，包括画布、地形层、区域形状、地点/建筑标记和明灯
    当前位置标记。函数不写数据库；地形与坐标来自结构字段：
    profile.rules.map、region.traits.map、place.coordinates。
    """
    profile_rules = profiles[0].get("rules") if profiles and isinstance(profiles[0].get("rules"), dict) else {}
    map_cfg = _nested_map_config(profile_rules)
    canvas = {
        **_DEFAULT_MAP_CANVAS,
        **{k: map_cfg.get(k) for k in ("width", "height", "unit", "projection", "title") if map_cfg.get(k) is not None},
    }
    terrain_layers: list[dict[str, Any]] = []
    for idx, layer in enumerate(map_cfg.get("terrain_layers") or []):
        if not isinstance(layer, dict):
            continue
        terrain = str(layer.get("terrain") or layer.get("key") or "custom")
        terrain_layers.append({
            "id": layer.get("id") or layer.get("key") or f"terrain.{idx}",
            "name": layer.get("name") or layer.get("label") or _TERRAIN_LABELS.get(terrain, terrain),
            "terrain": terrain,
            "terrain_label": _TERRAIN_LABELS.get(terrain, terrain),
            "x": _clamp_map_value(layer.get("x"), 0.0),
            "y": _clamp_map_value(layer.get("y"), 0.0),
            "width": _clamp_map_value(layer.get("width"), 100.0, 1.0, 100.0),
            "height": _clamp_map_value(layer.get("height"), 100.0, 1.0, 100.0),
            "source": "profile.rules.map.terrain_layers",
        })

    region_shapes = [_region_shape(region, i, len(regions)) for i, region in enumerate(regions)]
    region_shape_by_id = {str(r.get("id")): r for r in region_shapes if r.get("id")}
    markers = [_marker_from_place(place, i, region_shape_by_id) for i, place in enumerate(places)]
    terrains = terrain_layers + region_shapes
    actor = _actor_marker(markers, current_location, actor_label)
    return {
        "canvas": canvas,
        "terrain": terrains,
        "regions": region_shapes,
        "markers": markers,
        "actor": actor,
        "legend": {
            "terrain": [
                {"terrain": terrain, "label": label}
                for terrain, label in sorted({t.get("terrain"): t.get("terrain_label") for t in terrains}.items())
            ],
            "marker_roles": {
                "place": "地点",
                "important_building": "重要建筑",
            },
        },
        "counts": {
            "terrain": len(terrains),
            "markers": len(markers),
            "important_markers": len([m for m in markers if m.get("is_important")]),
        },
    }


_ARCHIVE_TABLES = {
    "profile": ("world_profiles", _decode_profile),
    "region": ("world_regions", _decode_region),
    "place": ("world_places", _decode_place),
    "lore": ("world_lore_entries", _decode_lore),
    "faction_presence": ("world_faction_presence", _decode_presence),
}


def _single_match(rows: list[Any]) -> Any | None:
    """只在精确匹配唯一时返回记录。

    输入来自 name/key 解析查询；输出是唯一 SQLite row 或 None。调用方是 location
    resolver。这里故意不在多匹配时猜测，避免把普通事件地点误绑到错误世界地点。
    """
    return rows[0] if len(rows) == 1 else None


def _lookup_region(conn, owner_kind: str, owner_id: str, *, region_id: str | None = None,
                   region_key: str | None = None) -> dict[str, Any]:
    """按 id 或 key 查找可生效区域。"""
    if region_id:
        return _decode_region(conn.execute(
            "SELECT * FROM world_regions WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (region_id, owner_kind, owner_id),
        ).fetchone())
    if region_key:
        return _decode_region(conn.execute(
            "SELECT * FROM world_regions WHERE key=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (region_key, owner_kind, owner_id),
        ).fetchone())
    return {}


def _lookup_place(conn, owner_kind: str, owner_id: str, *, place_id: str | None = None,
                  place_key: str | None = None) -> dict[str, Any]:
    """按 id 或 key 查找可生效地点。"""
    if place_id:
        return _decode_place(conn.execute(
            "SELECT * FROM world_places WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (place_id, owner_kind, owner_id),
        ).fetchone())
    if place_key:
        return _decode_place(conn.execute(
            "SELECT * FROM world_places WHERE key=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (place_key, owner_kind, owner_id),
        ).fetchone())
    return {}


def resolve_location_reference(conn, owner_kind: str, owner_id: str,
                               location: dict[str, Any] | None) -> dict[str, Any]:
    """把事件 location 解析为世界本体引用。

    输入是事件创建或上下文解析时传入的 location 字典；输出保留原字段，并在能唯一
    命中时补充 `world_place_id/world_place_key/world_region_id/world_region_key`。
    显式 world_* id/key 不存在时抛错，让写入方尽早发现坏引用；普通 name 只在唯一
    命中时自动绑定，多匹配或未命中则保持原样以兼容历史事件。
    """
    if not isinstance(location, dict):
        return {}
    loc = dict(location or {})
    if not loc:
        return loc

    explicit_place_id = loc.get("world_place_id")
    explicit_place_key = loc.get("world_place_key")
    explicit_region_id = loc.get("world_region_id")
    explicit_region_key = loc.get("world_region_key")

    place = _lookup_place(conn, owner_kind, owner_id, place_id=explicit_place_id, place_key=explicit_place_key)
    if (explicit_place_id or explicit_place_key) and not place:
        raise ValueError("world place reference not found")

    region = _lookup_region(conn, owner_kind, owner_id, region_id=explicit_region_id, region_key=explicit_region_key)
    if (explicit_region_id or explicit_region_key) and not region:
        raise ValueError("world region reference not found")

    if not place and (loc.get("place_id") or loc.get("place_key")):
        place = _lookup_place(conn, owner_kind, owner_id, place_id=loc.get("place_id"), place_key=loc.get("place_key"))
    if not region and (loc.get("region_id") or loc.get("region_key")):
        region = _lookup_region(conn, owner_kind, owner_id, region_id=loc.get("region_id"), region_key=loc.get("region_key"))

    name = str(loc.get("name") or loc.get("display_name") or "").strip()
    if not place and name:
        matched = _single_match(conn.execute(
            """SELECT * FROM world_places
               WHERE owner_kind=? AND owner_id=? AND status='active' AND (name=? OR key=?)""",
            (owner_kind, owner_id, name, name),
        ).fetchall())
        if matched:
            place = _decode_place(matched)
    region_name = str(loc.get("region_name") or loc.get("region") or "").strip()
    if not region and region_name:
        matched = _single_match(conn.execute(
            """SELECT * FROM world_regions
               WHERE owner_kind=? AND owner_id=? AND status='active' AND (name=? OR key=?)""",
            (owner_kind, owner_id, region_name, region_name),
        ).fetchall())
        if matched:
            region = _decode_region(matched)
    if place and not region and place.get("region_id"):
        region = _lookup_region(conn, owner_kind, owner_id, region_id=place.get("region_id"))
    structured_region_ref = any(loc.get(k) for k in ("world_region_id", "world_region_key", "region_id", "region_key"))
    if structured_region_ref and place and region and place.get("region_id") and place.get("region_id") != region.get("id"):
        raise ValueError("world place does not belong to the referenced world region")
    if not region and name and not place:
        matched = _single_match(conn.execute(
            """SELECT * FROM world_regions
               WHERE owner_kind=? AND owner_id=? AND status='active' AND (name=? OR key=?)""",
            (owner_kind, owner_id, name, name),
        ).fetchall())
        if matched:
            region = _decode_region(matched)

    if place:
        loc["world_place_id"] = place.get("id")
        loc["world_place_key"] = place.get("key")
        loc.setdefault("world_place_name", place.get("name"))
    if region:
        loc["world_region_id"] = region.get("id")
        loc["world_region_key"] = region.get("key")
        loc.setdefault("world_region_name", region.get("name"))
    return loc


def _validate_scope(conn, owner_kind: str, owner_id: str, scope_kind: str | None, scope_id: str | None) -> tuple[str, str]:
    """校验世界条目的作用域。

    输入来自 lore 或势力影响记录；输出规整后的 `(scope_kind, scope_id)`。函数只读
    DB，用结构化 region/place id 验证文本内容作用范围，避免 lore 靠提示词描述
    自己“应该作用于哪里”。失败时抛错，由外层 LifeOps savepoint 回滚。
    """
    kind = str(scope_kind or "world").strip()
    sid = str(scope_id or WORLD_SCOPE_ID).strip()
    if kind not in _SCOPE_KINDS:
        raise ValueError("scope_kind must be world/region/place")
    if kind == "world":
        return kind, WORLD_SCOPE_ID
    table = "world_regions" if kind == "region" else "world_places"
    row = conn.execute(
        f"SELECT id FROM {table} WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
        (sid, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"{kind} scope not found: {sid}")
    return kind, sid


def upsert_world_profile(conn, owner_kind: str, owner_id: str, *, key: str = "default",
                         title: str | None = None, summary: str | None = None,
                         background_text: str | None = None, rules: dict[str, Any] | None = None,
                         evidence: dict[str, Any] | None = None,
                         status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新世界观档案。

    输入来自 `life_world profile/upsert_profile` 的 LifeOps；输出是档案记录。档案
    是某个 owner 世界观的顶层背景和规则文字，生命周期随 owner 本地 DB 持久化；
    事件、上下文、WebUI 和世界条目摘要会读取它。副作用是写 world_profiles 与
    journal；同一 owner/key 幂等更新，禁止使用提示词承诺替代结构化 key。
    """
    key = _clean_key(key, "default")
    title = str(title or key).strip()
    if status not in {"active", "archived"}:
        raise ValueError("world profile status must be active/archived")
    existing = conn.execute(
        "SELECT id FROM world_profiles WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        profile_id = existing["id"]
        conn.execute(
            """UPDATE world_profiles
               SET title=?, summary=?, background_text=?, rules_json=?, evidence_json=?,
                   status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (title, summary, background_text, dumps(rules or {}), dumps(evidence or {}),
             status, source, profile_id),
        )
        event_type = "world_profile_updated"
    else:
        profile_id = new_id("worldprofile")
        conn.execute(
            """INSERT INTO world_profiles(
                 id, owner_kind, owner_id, key, title, summary, background_text,
                 rules_json, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (profile_id, owner_kind, owner_id, key, title, summary, background_text,
             dumps(rules or {}), dumps(evidence or {}), status, source),
        )
        event_type = "world_profile_created"
    append_journal(conn, owner_kind, owner_id, event_type, {"profile_id": profile_id, "key": key}, source)
    return _decode_profile(conn.execute("SELECT * FROM world_profiles WHERE id=?", (profile_id,)).fetchone())


def upsert_region(conn, owner_kind: str, owner_id: str, *, key: str, name: str,
                  region_type: str = "region", parent_region_id: str | None = None,
                  summary: str | None = None, content: str | None = None,
                  traits: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None,
                  status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新世界地图区域。

    输入是稳定 key、名称和可选父区域；输出是区域记录。区域用于表达大陆、城区、
    城池、街区等地图层级，供地点、事件 location、势力影响和 lore 结构化引用。
    副作用是写 world_regions 与 journal；父区域必须真实存在，避免只靠文本描述
    地图层级。status 为 active/archived。
    """
    key = _clean_key(key)
    name = str(name or "").strip()
    if not name:
        raise ValueError("region name is required")
    if status not in {"active", "archived"}:
        raise ValueError("region status must be active/archived")
    if parent_region_id:
        parent = conn.execute(
            "SELECT id FROM world_regions WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (parent_region_id, owner_kind, owner_id),
        ).fetchone()
        if not parent:
            raise ValueError(f"parent region not found: {parent_region_id}")
    existing = conn.execute(
        "SELECT id FROM world_regions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        region_id = existing["id"]
        conn.execute(
            """UPDATE world_regions
               SET name=?, region_type=?, parent_region_id=?, summary=?, content=?,
                   traits_json=?, evidence_json=?, status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (name, region_type or "region", parent_region_id, summary, content,
             dumps(traits or {}), dumps(evidence or {}), status, source, region_id),
        )
        event_type = "world_region_updated"
    else:
        region_id = new_id("worldregion")
        conn.execute(
            """INSERT INTO world_regions(
                 id, owner_kind, owner_id, key, name, region_type, parent_region_id,
                 summary, content, traits_json, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (region_id, owner_kind, owner_id, key, name, region_type or "region", parent_region_id,
             summary, content, dumps(traits or {}), dumps(evidence or {}), status, source),
        )
        event_type = "world_region_created"
    append_journal(conn, owner_kind, owner_id, event_type, {"region_id": region_id, "key": key}, source)
    return _decode_region(conn.execute("SELECT * FROM world_regions WHERE id=?", (region_id,)).fetchone())


def upsert_place(conn, owner_kind: str, owner_id: str, *, key: str, name: str,
                 place_type: str = "place", region_id: str | None = None,
                 parent_place_id: str | None = None, summary: str | None = None,
                 content: str | None = None, coordinates: dict[str, Any] | None = None,
                 traits: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None,
                 status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新具体地点。

    输入来自世界观工具或投影逻辑；输出地点记录。地点承载街道、店铺、道观、城门
    等可被事件和日程引用的位置，文本描述放 content，结构作用域由 region_id /
    parent_place_id / coordinates 控制。写入幂等按 owner/key，失败由 LifeOps 回滚。
    """
    key = _clean_key(key)
    name = str(name or "").strip()
    if not name:
        raise ValueError("place name is required")
    if status not in {"active", "archived"}:
        raise ValueError("place status must be active/archived")
    if region_id:
        region = conn.execute(
            "SELECT id FROM world_regions WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (region_id, owner_kind, owner_id),
        ).fetchone()
        if not region:
            raise ValueError(f"region not found: {region_id}")
    if parent_place_id:
        parent = conn.execute(
            "SELECT id FROM world_places WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
            (parent_place_id, owner_kind, owner_id),
        ).fetchone()
        if not parent:
            raise ValueError(f"parent place not found: {parent_place_id}")
    existing = conn.execute(
        "SELECT id FROM world_places WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        place_id = existing["id"]
        conn.execute(
            """UPDATE world_places
               SET name=?, place_type=?, region_id=?, parent_place_id=?, summary=?, content=?,
                   coordinates_json=?, traits_json=?, evidence_json=?, status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (name, place_type or "place", region_id, parent_place_id, summary, content,
             dumps(coordinates or {}), dumps(traits or {}), dumps(evidence or {}),
             status, source, place_id),
        )
        event_type = "world_place_updated"
    else:
        place_id = new_id("worldplace")
        conn.execute(
            """INSERT INTO world_places(
                 id, owner_kind, owner_id, key, name, place_type, region_id, parent_place_id,
                 summary, content, coordinates_json, traits_json, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (place_id, owner_kind, owner_id, key, name, place_type or "place", region_id, parent_place_id,
             summary, content, dumps(coordinates or {}), dumps(traits or {}), dumps(evidence or {}),
             status, source),
        )
        event_type = "world_place_created"
    append_journal(conn, owner_kind, owner_id, event_type, {"place_id": place_id, "key": key}, source)
    return _decode_place(conn.execute("SELECT * FROM world_places WHERE id=?", (place_id,)).fetchone())


def upsert_lore_entry(conn, owner_kind: str, owner_id: str, *, key: str, title: str,
                      lore_type: str = "background", scope_kind: str = "world",
                      scope_id: str | None = None, content: str | None = None,
                      tags: list[str] | None = None, evidence: dict[str, Any] | None = None,
                      status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新世界观知识条目。

    输入是稳定 key、标题、类型、作用域和正文；输出 lore 记录。正文可以是自然
    语言，但 scope_kind/scope_id 是结构化生效范围，调用方可据此把条目只注入到
    相关地点/区域/世界场景。status 支持 active/archived，写入走 journal。
    """
    key = _clean_key(key)
    title = str(title or "").strip()
    if not title:
        raise ValueError("lore title is required")
    if status not in {"active", "archived"}:
        raise ValueError("lore status must be active/archived")
    scope_kind, scope_id = _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)
    existing = conn.execute(
        "SELECT id FROM world_lore_entries WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        lore_id = existing["id"]
        conn.execute(
            """UPDATE world_lore_entries
               SET title=?, lore_type=?, scope_kind=?, scope_id=?, content=?,
                   tags_json=?, evidence_json=?, status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (title, lore_type or "background", scope_kind, scope_id, content,
             dumps(tags or []), dumps(evidence or {}), status, source, lore_id),
        )
        event_type = "world_lore_updated"
    else:
        lore_id = new_id("worldlore")
        conn.execute(
            """INSERT INTO world_lore_entries(
                 id, owner_kind, owner_id, key, title, lore_type, scope_kind, scope_id,
                 content, tags_json, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lore_id, owner_kind, owner_id, key, title, lore_type or "background", scope_kind, scope_id,
             content, dumps(tags or []), dumps(evidence or {}), status, source),
        )
        event_type = "world_lore_created"
    append_journal(conn, owner_kind, owner_id, event_type, {"lore_id": lore_id, "key": key, "scope_kind": scope_kind, "scope_id": scope_id}, source)
    return _decode_lore(conn.execute("SELECT * FROM world_lore_entries WHERE id=?", (lore_id,)).fetchone())


def upsert_faction_presence(conn, owner_kind: str, owner_id: str, *, faction_entity_id: str,
                            scope_kind: str = "world", scope_id: str | None = None,
                            influence: float = 0.0, stance: str | None = None,
                            summary: str | None = None, content: str | None = None,
                            evidence: dict[str, Any] | None = None,
                            status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新势力在世界范围内的影响记录。

    输入是 social_world 中的 faction/entity id 与结构化作用域；输出影响记录。
    该函数把“势力在哪里起作用”从文本描述变成可查询账本，供委托、流言、事件
    生成和 WebUI 查询。faction_entity_id 必须存在于 world_entities，避免孤儿势力。
    """
    faction = conn.execute(
        "SELECT id, display_name FROM world_entities WHERE id=? AND owner_kind=? AND owner_id=? AND status!='archived'",
        (faction_entity_id, owner_kind, owner_id),
    ).fetchone()
    if not faction:
        raise ValueError(f"faction entity not found: {faction_entity_id}")
    if status not in {"active", "archived"}:
        raise ValueError("faction presence status must be active/archived")
    scope_kind, scope_id = _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)
    influence_v = max(-100.0, min(100.0, float(influence or 0)))
    existing = conn.execute(
        """SELECT id FROM world_faction_presence
           WHERE owner_kind=? AND owner_id=? AND faction_entity_id=? AND scope_kind=? AND scope_id=?""",
        (owner_kind, owner_id, faction_entity_id, scope_kind, scope_id),
    ).fetchone()
    if existing:
        presence_id = existing["id"]
        conn.execute(
            """UPDATE world_faction_presence
               SET influence=?, stance=?, summary=?, content=?, evidence_json=?,
                   status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (influence_v, stance, summary, content, dumps(evidence or {}), status, source, presence_id),
        )
        event_type = "world_faction_presence_updated"
    else:
        presence_id = new_id("worldpresence")
        conn.execute(
            """INSERT INTO world_faction_presence(
                 id, owner_kind, owner_id, faction_entity_id, scope_kind, scope_id,
                 influence, stance, summary, content, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (presence_id, owner_kind, owner_id, faction_entity_id, scope_kind, scope_id,
             influence_v, stance, summary, content, dumps(evidence or {}), status, source),
        )
        event_type = "world_faction_presence_created"
    append_journal(conn, owner_kind, owner_id, event_type, {"presence_id": presence_id, "faction_entity_id": faction_entity_id, "scope_kind": scope_kind, "scope_id": scope_id}, source)
    return _decode_presence(conn.execute("SELECT * FROM world_faction_presence WHERE id=?", (presence_id,)).fetchone())


def _active_dependents(conn, owner_kind: str, owner_id: str, kind: str, object_id: str) -> list[dict[str, Any]]:
    """查询归档对象的 active 依赖。

    输入是待归档对象类型和 id；输出是仍处于 active 的直接依赖列表。调用方是
    archive_object；默认用它阻止会产生 orphan 的删除，cascade=True 时作为级联清单。
    """
    deps: list[dict[str, Any]] = []
    if kind == "region":
        checks = [
            ("region", "world_regions", "parent_region_id"),
            ("place", "world_places", "region_id"),
            ("lore", "world_lore_entries", None),
            ("faction_presence", "world_faction_presence", None),
        ]
        for dep_kind, table, column in checks:
            if column:
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE owner_kind=? AND owner_id=? AND status='active' AND {column}=?",
                    (owner_kind, owner_id, object_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE owner_kind=? AND owner_id=? AND status='active' AND scope_kind='region' AND scope_id=?",
                    (owner_kind, owner_id, object_id),
                ).fetchall()
            deps.extend({"kind": dep_kind, "id": r["id"]} for r in rows)
    elif kind == "place":
        checks = [
            ("place", "world_places", "parent_place_id"),
            ("lore", "world_lore_entries", None),
            ("faction_presence", "world_faction_presence", None),
        ]
        for dep_kind, table, column in checks:
            if column:
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE owner_kind=? AND owner_id=? AND status='active' AND {column}=?",
                    (owner_kind, owner_id, object_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE owner_kind=? AND owner_id=? AND status='active' AND scope_kind='place' AND scope_id=?",
                    (owner_kind, owner_id, object_id),
                ).fetchall()
            deps.extend({"kind": dep_kind, "id": r["id"]} for r in rows)
    return deps


def _archive_by_id(conn, owner_kind: str, owner_id: str, kind: str, object_id: str,
                   source: str, archived: list[dict[str, Any]],
                   seen: set[tuple[str, str]] | None = None) -> None:
    """按 id 级联归档世界本体对象。

    输入来自 archive_object 的 cascade 流程；输出写入 archived 收集列表。函数只接受
    内部 kind/table 白名单，副作用是更新 status 与 journal。它保证先归档子节点和
    作用域条目，再归档父节点，避免 active orphan。
    """
    seen = seen or set()
    mark = (kind, object_id)
    if mark in seen:
        return
    seen.add(mark)
    table, _decoder = _ARCHIVE_TABLES[kind]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id=? AND owner_kind=? AND owner_id=?",
        (object_id, owner_kind, owner_id),
    ).fetchone()
    if not row or row["status"] == "archived":
        return
    for dep in _active_dependents(conn, owner_kind, owner_id, kind, object_id):
        _archive_by_id(conn, owner_kind, owner_id, dep["kind"], dep["id"], source, archived, seen)
    conn.execute(f"UPDATE {table} SET status='archived', updated_at=datetime('now') WHERE id=?", (object_id,))
    archived.append({"object_kind": kind, "object_id": object_id})
    append_journal(conn, owner_kind, owner_id, f"world_{kind}_archived", {"object_kind": kind, "object_id": object_id}, source)


def archive_object(conn, owner_kind: str, owner_id: str, *, object_kind: str,
                   object_id: str | None = None, key: str | None = None,
                   faction_entity_id: str | None = None, scope_kind: str | None = None,
                   scope_id: str | None = None, cascade: bool = False,
                   source: str = "life_world") -> dict[str, Any]:
    """归档一个世界本体对象。

    输入是对象类型和稳定 id/key；输出归档后的记录。这里执行软删除，只把 status 置为
    archived 并写 journal，不物理删除历史设定，避免世界观修改破坏既有事件证据链。
    """
    kind = str(object_kind or "").strip().lower()
    if kind not in _ARCHIVE_TABLES:
        raise ValueError("object_kind must be profile/region/place/lore/faction_presence")
    table, decoder = _ARCHIVE_TABLES[kind]
    params: list[Any] = [owner_kind, owner_id]
    where = "owner_kind=? AND owner_id=?"
    if object_id:
        where += " AND id=?"
        params.append(object_id)
    elif key and kind != "faction_presence":
        where += " AND key=?"
        params.append(key)
    elif kind == "faction_presence" and faction_entity_id:
        sk, sid = _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)
        where += " AND faction_entity_id=? AND scope_kind=? AND scope_id=?"
        params.extend([faction_entity_id, sk, sid])
    else:
        raise ValueError("archive requires object_id, key, or faction scope")
    row = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(params)).fetchone()
    if not row:
        raise ValueError(f"world {kind} not found")
    deps = _active_dependents(conn, owner_kind, owner_id, kind, row["id"])
    if deps and not cascade:
        raise ValueError(f"world {kind} has active dependents; pass cascade=true to archive them")
    archived: list[dict[str, Any]] = []
    _archive_by_id(conn, owner_kind, owner_id, kind, row["id"], source, archived)
    out = decoder(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone())
    out["archived_dependents"] = [item for item in archived if item["object_id"] != row["id"]]
    return out


def _collect_effective_entries(conn, owner_kind: str, owner_id: str, scopes: list[tuple[str, str]],
                               list_fn, limit: int) -> list[dict[str, Any]]:
    """按当前场景优先级收集可生效条目。

    输入是按“最具体到最通用”排序的 scope；输出最多 limit 条记录。调用方是
    effective_context。这里保证 place/region 级条目不会被大量 world 级条目挤掉。
    """
    out: list[dict[str, Any]] = []
    limit_v = max(1, int(limit))
    for scope_kind, scope_id in scopes:
        if len(out) >= limit_v:
            break
        out.extend(list_fn(
            conn, owner_kind, owner_id,
            scope_kind=scope_kind, scope_id=scope_id,
            limit=limit_v,
        ))
    return out[:limit_v]


def list_profiles(conn, owner_kind: str, owner_id: str, *, status: str | None = "active", limit: int = 20) -> list[dict[str, Any]]:
    """列出世界档案。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(f"SELECT * FROM world_profiles {where} ORDER BY updated_at DESC LIMIT ?", tuple(params + [int(limit)])).fetchall()
    return [_decode_profile(r) for r in rows]


def list_regions(conn, owner_kind: str, owner_id: str, *, parent_region_id: str | None = None,
                 status: str | None = "active", limit: int = 100) -> list[dict[str, Any]]:
    """列出世界区域。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if parent_region_id is not None:
        where += " AND parent_region_id=?"
        params.append(parent_region_id)
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(f"SELECT * FROM world_regions {where} ORDER BY parent_region_id, name LIMIT ?", tuple(params + [int(limit)])).fetchall()
    return [_decode_region(r) for r in rows]


def list_places(conn, owner_kind: str, owner_id: str, *, region_id: str | None = None,
                parent_place_id: str | None = None, status: str | None = "active",
                limit: int = 100) -> list[dict[str, Any]]:
    """列出世界地点。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if region_id:
        where += " AND region_id=?"
        params.append(region_id)
    if parent_place_id:
        where += " AND parent_place_id=?"
        params.append(parent_place_id)
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(f"SELECT * FROM world_places {where} ORDER BY region_id, name LIMIT ?", tuple(params + [int(limit)])).fetchall()
    return [_decode_place(r) for r in rows]


def list_lore_entries(conn, owner_kind: str, owner_id: str, *, scope_kind: str | None = None,
                      scope_id: str | None = None, lore_type: str | None = None,
                      status: str | None = "active", limit: int = 80) -> list[dict[str, Any]]:
    """列出知识条目，可按结构作用域过滤。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if scope_kind:
        where += " AND scope_kind=?"
        params.append(scope_kind)
    if scope_id:
        where += " AND scope_id=?"
        params.append(scope_id)
    if lore_type:
        where += " AND lore_type=?"
        params.append(lore_type)
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(f"SELECT * FROM world_lore_entries {where} ORDER BY updated_at DESC LIMIT ?", tuple(params + [int(limit)])).fetchall()
    return [_decode_lore(r) for r in rows]


def list_faction_presence(conn, owner_kind: str, owner_id: str, *, faction_entity_id: str | None = None,
                          scope_kind: str | None = None, scope_id: str | None = None,
                          status: str | None = "active", limit: int = 80) -> list[dict[str, Any]]:
    """列出势力影响，可按结构作用域过滤。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE p.owner_kind=? AND p.owner_id=?"
    if faction_entity_id:
        where += " AND p.faction_entity_id=?"
        params.append(faction_entity_id)
    if scope_kind:
        where += " AND p.scope_kind=?"
        params.append(scope_kind)
    if scope_id:
        where += " AND p.scope_id=?"
        params.append(scope_id)
    if status:
        where += " AND p.status=?"
        params.append(status)
    rows = conn.execute(
        f"""SELECT p.*, e.display_name AS faction_name, e.entity_kind AS faction_kind
            FROM world_faction_presence p
            LEFT JOIN world_entities e ON e.id=p.faction_entity_id
            {where}
            ORDER BY ABS(p.influence) DESC, p.updated_at DESC LIMIT ?""",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_presence(r) for r in rows]


def effective_context(conn, owner_kind: str, owner_id: str, *, region_id: str | None = None,
                      region_key: str | None = None, place_id: str | None = None,
                      place_key: str | None = None, location: dict[str, Any] | None = None,
                      limit: int = 20) -> dict[str, Any]:
    """按结构作用域解析当前场景可生效的世界观。

    输入可以是显式 region/place id 或 key，也可以是事件 location 字典；输出只包含
    world、命中 region、命中 place 三层作用域内的档案、知识条目和势力影响。正文仍
    是文字，但能否被取出由 scope_kind/scope_id 和地点/区域结构决定，而不是提示词
    里写“请只在这里生效”。
    """
    loc = resolve_location_reference(conn, owner_kind, owner_id, location if isinstance(location, dict) else {})
    rid = region_id or loc.get("world_region_id") or loc.get("region_id")
    rkey = region_key or loc.get("world_region_key") or loc.get("region_key")
    pid = place_id or loc.get("world_place_id") or loc.get("place_id")
    pkey = place_key or loc.get("world_place_key") or loc.get("place_key")
    place = _lookup_place(conn, owner_kind, owner_id, place_id=pid, place_key=pkey)
    if place and not rid and not rkey:
        rid = place.get("region_id")
    region = _lookup_region(conn, owner_kind, owner_id, region_id=rid, region_key=rkey)

    activation_scopes: list[tuple[str, str]] = [("world", WORLD_SCOPE_ID)]
    if region.get("id"):
        activation_scopes.append(("region", region["id"]))
    if place.get("id"):
        activation_scopes.append(("place", place["id"]))
    effective_scopes = list(reversed(activation_scopes))
    limit_v = max(1, int(limit))
    lore = _collect_effective_entries(conn, owner_kind, owner_id, effective_scopes, list_lore_entries, limit_v)
    presence = _collect_effective_entries(conn, owner_kind, owner_id, effective_scopes, list_faction_presence, limit_v)

    profiles = list_profiles(conn, owner_kind, owner_id, limit=2)
    return {
        "activation": {
            "scope_ids": [{"scope_kind": kind, "scope_id": sid} for kind, sid in activation_scopes],
            "scope_priority": [{"scope_kind": kind, "scope_id": sid} for kind, sid in effective_scopes],
            "region_id": region.get("id"),
            "region_key": region.get("key"),
            "place_id": place.get("id"),
            "place_key": place.get("key"),
        },
        "profiles": profiles,
        "regions": [region] if region else [],
        "places": [place] if place else [],
        "lore": lore,
        "faction_presence": presence,
        "counts": {
            "profiles": len(profiles),
            "regions": 1 if region else 0,
            "places": 1 if place else 0,
            "lore": len(lore),
            "faction_presence": len(presence),
        },
    }


def summary(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    """读取世界本体紧凑摘要。

    输入是 owner 和条数上限；输出用于 context/WebUI/tool read 的结构化世界状态。
    函数只读，不写数据库。摘要刻意保留文本字段，但按 profile/region/place/lore/
    faction_presence 分层，调用方可以按结构选择需要的世界观片段。
    """
    profiles = list_profiles(conn, owner_kind, owner_id, limit=5)
    regions = list_regions(conn, owner_kind, owner_id, limit=limit)
    places = list_places(conn, owner_kind, owner_id, limit=limit)
    lore = list_lore_entries(conn, owner_kind, owner_id, limit=limit)
    presence = list_faction_presence(conn, owner_kind, owner_id, limit=limit)
    return {
        "profiles": profiles,
        "regions": regions,
        "places": places,
        "lore": lore,
        "faction_presence": presence,
        "map": map_state(profiles, regions, places),
        "counts": {
            "profiles": len(profiles),
            "regions": len(regions),
            "places": len(places),
            "lore": len(lore),
            "faction_presence": len(presence),
        },
    }
