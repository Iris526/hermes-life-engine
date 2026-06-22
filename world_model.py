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

_DEFAULT_MAP_CANVAS = {
    "width": 100,
    "height": 100,
    "unit": "grid",
    "projection": "local_grid",
    "title": "世界地图",
    "background_color": "#15181c",
}
_DEFAULT_MAP_VIEWPORT = {
    "min_zoom": 0.75,
    "max_zoom": 6.0,
    "default_zoom": 1.0,
    "zoom_step": 1.25,
    "pan_enabled": True,
    "marking_enabled": True,
}
_DEFAULT_MAP_GRID = {"visible": True, "size": 10, "major_every": 5}
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
_MARKER_ROLE_LABELS = {
    "place": "地点",
    "important_building": "重要建筑",
    "quest": "任务",
    "danger": "危险",
    "resource": "资源",
    "camp": "营地",
    "portal": "传送点",
    "custom": "标记",
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


def _decode_route(row) -> dict[str, Any]:
    """解码世界路线行。"""
    item = _decode_json_fields(_row(row), ["cost_json", "schedule_json", "traits_json", "evidence_json"])
    if "points_json" in item:
        item["points"] = loads(item.pop("points_json"), [])
    return item


def _decode_condition(row) -> dict[str, Any]:
    """解码动态世界状态行。"""
    return _decode_json_fields(_row(row), ["payload_json", "evidence_json"])


def _decode_chronicle_event(row) -> dict[str, Any]:
    """解码世界编年史事件行。"""
    item = _decode_json_fields(_row(row), ["related_json", "evidence_json"])
    if "tags_json" in item:
        item["tags"] = loads(item.pop("tags_json"), [])
    return item


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


def _positive_map_value(value: Any, default: float, minimum: float = 1.0, maximum: float = 10000.0) -> float:
    """读取地图画布/缩放等正数配置。

    输入来自 profile.rules.map 的画布、网格或视口字段；输出是受限正数。调用方是
    地图契约归一化流程。异常或越界值会回落/夹取，保证旧档案和手写配置不会让
    WebUI 地图崩溃。
    """
    return _clamp_map_value(value, default, minimum, maximum)


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


def _map_canvas(map_cfg: dict[str, Any]) -> dict[str, Any]:
    """生成地图画布结构。

    输入是 profile.rules.map；输出是 WebUI 与上下文共享的 canvas。width/height 是
    地图坐标系范围，不是 CSS 像素；所有区域、路线、标记都在这个坐标系内解释。
    调用方是 map_state；无副作用。
    """
    canvas = dict(_DEFAULT_MAP_CANVAS)
    canvas["width"] = _positive_map_value(map_cfg.get("width"), _DEFAULT_MAP_CANVAS["width"], 10.0)
    canvas["height"] = _positive_map_value(map_cfg.get("height"), _DEFAULT_MAP_CANVAS["height"], 10.0)
    for key in ("unit", "projection", "title", "background_color"):
        if map_cfg.get(key) is not None:
            canvas[key] = map_cfg.get(key)
    return canvas


def _map_viewport(map_cfg: dict[str, Any], canvas: dict[str, Any]) -> dict[str, Any]:
    """生成地图视口能力结构。

    输入是 profile.rules.map.viewport 与 canvas；输出描述默认缩放、缩放上下限、默认
    中心点和交互开关。调用方是 WebUI 地图渲染器；它只表达能力，不保存用户临时
    pan/zoom 状态。
    """
    raw = map_cfg.get("viewport") if isinstance(map_cfg.get("viewport"), dict) else {}
    viewport = dict(_DEFAULT_MAP_VIEWPORT)
    viewport["min_zoom"] = _positive_map_value(raw.get("min_zoom"), viewport["min_zoom"], 0.1, 20.0)
    viewport["max_zoom"] = max(
        viewport["min_zoom"],
        _positive_map_value(raw.get("max_zoom"), viewport["max_zoom"], viewport["min_zoom"], 50.0),
    )
    viewport["default_zoom"] = _clamp_map_value(
        raw.get("default_zoom"),
        viewport["default_zoom"],
        viewport["min_zoom"],
        viewport["max_zoom"],
    )
    viewport["zoom_step"] = _positive_map_value(raw.get("zoom_step"), viewport["zoom_step"], 1.01, 4.0)
    center = raw.get("default_center") if isinstance(raw.get("default_center"), dict) else {}
    viewport["default_center"] = {
        "x": _clamp_map_value(center.get("x"), canvas["width"] / 2, 0.0, canvas["width"]),
        "y": _clamp_map_value(center.get("y"), canvas["height"] / 2, 0.0, canvas["height"]),
    }
    viewport["pan_enabled"] = bool(raw.get("pan_enabled", viewport["pan_enabled"]))
    viewport["marking_enabled"] = bool(raw.get("marking_enabled", viewport["marking_enabled"]))
    return viewport


def _map_grid(map_cfg: dict[str, Any], canvas: dict[str, Any]) -> dict[str, Any]:
    """生成地图网格配置。

    输入是 profile.rules.map.grid；输出是可渲染网格设置。调用方是 WebUI SVG 地图。
    size 的单位与 canvas 一致，默认按较短边的十分之一生成，避免任意画布尺寸下
    网格过密或过疏。
    """
    raw = map_cfg.get("grid") if isinstance(map_cfg.get("grid"), dict) else {}
    default_size = max(1.0, min(canvas["width"], canvas["height"]) / 10.0)
    return {
        "visible": bool(raw.get("visible", _DEFAULT_MAP_GRID["visible"])),
        "size": _positive_map_value(raw.get("size"), default_size, 0.1, max(canvas["width"], canvas["height"])),
        "major_every": int(_positive_map_value(raw.get("major_every"), _DEFAULT_MAP_GRID["major_every"], 1.0, 20.0)),
    }


def _iter_map_records(raw: Any) -> list[dict[str, Any]]:
    """把 list/dict 形式的地图配置规整成对象列表。

    输入来自 rules.map.assets、image_layers、routes 等用户可编辑字段；输出是 dict
    列表。调用方只读这些对象；非法项会被跳过以保持兼容。
    """
    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if isinstance(value, dict):
                items.append({"id": key, **value})
        return items
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []


def _map_assets(map_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """生成地图图片资源引用列表。

    输入是 profile.rules.map.assets 或兼容的单底图字段；输出为 asset 结构，包含
    id/name/kind/href/path。href 可以是 URL、/static 路径、data:image 或交给
    WebUI /api/asset 解析的本地资源路径。该函数不读文件、不校验网络，只归一化
    引用契约。
    """
    assets: list[dict[str, Any]] = []
    for idx, asset in enumerate(_iter_map_records(map_cfg.get("assets") or map_cfg.get("asset_refs"))):
        asset_id = str(asset.get("id") or asset.get("key") or f"asset.{idx}")
        href = asset.get("href") or asset.get("url") or asset.get("uri") or asset.get("path")
        assets.append({
            "id": asset_id,
            "name": asset.get("name") or asset.get("label") or asset_id,
            "kind": asset.get("kind") or "image",
            "href": href,
            "path": asset.get("path"),
            "attribution": asset.get("attribution"),
        })
    background = map_cfg.get("background_image") or map_cfg.get("image") or map_cfg.get("asset_path")
    if background and not any(asset.get("id") == "base_map" for asset in assets):
        assets.insert(0, {"id": "base_map", "name": "底图", "kind": "image", "href": background, "path": background})
    return assets


def _rect_from_map_record(record: dict[str, Any], canvas: dict[str, Any]) -> dict[str, float]:
    """从地图配置记录读取矩形范围。

    输入是图片图层、地形层或路线附属 bounds；输出 x/y/width/height。调用方负责
    把记录渲染到 canvas 坐标系。bounds 子结构和顶层字段都兼容。
    """
    bounds = record.get("bounds") if isinstance(record.get("bounds"), dict) else {}
    merged = {**bounds, **record}
    width = _clamp_map_value(_coord_value(merged, "width", canvas["width"]), canvas["width"], 0.1, canvas["width"])
    height = _clamp_map_value(_coord_value(merged, "height", canvas["height"]), canvas["height"], 0.1, canvas["height"])
    x = _clamp_map_value(_coord_value(merged, "x", 0.0), 0.0, 0.0, max(0.0, canvas["width"] - width))
    y = _clamp_map_value(_coord_value(merged, "y", 0.0), 0.0, 0.0, max(0.0, canvas["height"] - height))
    return {"x": x, "y": y, "width": width, "height": height}


def _map_image_layers(map_cfg: dict[str, Any], assets: list[dict[str, Any]], canvas: dict[str, Any]) -> list[dict[str, Any]]:
    """生成地图图片图层。

    输入是 profile.rules.map.image_layers 和 assets；输出可按顺序渲染的 image layer。
    当只配置了底图 asset 而没有显式 image_layers 时，自动生成铺满画布的 base layer。
    调用方是 map_state；无文件读写副作用。
    """
    asset_by_id = {asset.get("id"): asset for asset in assets}
    raw_layers = _iter_map_records(map_cfg.get("image_layers") or map_cfg.get("layers"))
    if not raw_layers and assets:
        base = assets[0]
        raw_layers = [{"id": "base", "name": base.get("name") or "底图", "asset_id": base.get("id")}]
    layers = []
    for idx, layer in enumerate(raw_layers):
        asset_id = layer.get("asset_id") or layer.get("asset") or layer.get("ref")
        asset = asset_by_id.get(asset_id) if asset_id else {}
        href = layer.get("href") or layer.get("url") or layer.get("uri") or layer.get("path") or asset.get("href")
        if not href:
            continue
        rect = _rect_from_map_record(layer, canvas)
        layers.append({
            "id": layer.get("id") or layer.get("key") or f"image_layer.{idx}",
            "name": layer.get("name") or layer.get("label") or asset.get("name") or "地图图层",
            "asset_id": asset_id,
            "href": href,
            "opacity": _clamp_map_value(layer.get("opacity"), 1.0, 0.0, 1.0),
            "blend_mode": layer.get("blend_mode") or "normal",
            "order": int(_as_float(layer.get("order"), idx)),
            "source": "profile.rules.map.image_layers",
            **rect,
        })
    return sorted(layers, key=lambda item: item.get("order", 0))


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


def _region_shape(region: dict[str, Any], index: int, total: int, canvas: dict[str, Any]) -> dict[str, Any]:
    """把区域记录转换为地图地形块。

    输入是解码后的 region、列表序号和地图 canvas；输出包含 x/y/width/height/terrain
    的绘图结构。显式坐标来自 traits.map 或 traits；缺省时按序号生成稳定布局，
    保证 WebUI 仍有实际地图可显示，同时通过 explicit_position 标明是否人工定位。
    """
    traits = region.get("traits") if isinstance(region.get("traits"), dict) else {}
    map_cfg = _nested_map_config(traits)
    explicit = any(key in traits or key in map_cfg for key in {"x", "y", "width", "height", "w", "h"})
    columns = max(1, min(3, total or 1))
    col = index % columns
    row = index // columns
    canvas_w = float(canvas.get("width") or _DEFAULT_MAP_CANVAS["width"])
    canvas_h = float(canvas.get("height") or _DEFAULT_MAP_CANVAS["height"])
    default_w = canvas_w * 0.88 / columns
    default_h = canvas_h * 0.28
    default_x = canvas_w * 0.06 + col * (default_w + canvas_w * 0.03)
    default_y = canvas_h * 0.08 + row * (default_h + canvas_h * 0.05)
    width = _clamp_map_value(_coord_value(traits, "width", default_w), default_w, max(1.0, canvas_w * 0.08), canvas_w)
    height = _clamp_map_value(_coord_value(traits, "height", default_h), default_h, max(1.0, canvas_h * 0.08), canvas_h)
    x = _clamp_map_value(_coord_value(traits, "x", default_x), default_x, 0.0, max(0.0, canvas_w - width))
    y = _clamp_map_value(_coord_value(traits, "y", default_y), default_y, 0.0, max(0.0, canvas_h - height))
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
                       region_shapes: dict[str, dict[str, Any]],
                       canvas: dict[str, Any]) -> dict[str, Any]:
    """把地点记录转换为地图标记。

    输入是解码后的 place 和所属区域形状；输出是可绘制 marker。坐标优先来自
    coordinates.x/y，其次从所属 region 的矩形内稳定派生。调用方是 map_state；
    副作用为无。坐标解释使用 canvas 坐标系。
    """
    coordinates = place.get("coordinates") if isinstance(place.get("coordinates"), dict) else {}
    traits = place.get("traits") if isinstance(place.get("traits"), dict) else {}
    region = region_shapes.get(str(place.get("region_id") or "")) or {}
    canvas_w = float(canvas.get("width") or _DEFAULT_MAP_CANVAS["width"])
    canvas_h = float(canvas.get("height") or _DEFAULT_MAP_CANVAS["height"])
    explicit = any(key in coordinates or key in _nested_map_config(coordinates) for key in {"x", "y", "grid_x", "grid_y"})
    if explicit:
        x = _clamp_map_value(_coord_value(coordinates, "x", canvas_w / 2), canvas_w / 2, 0.0, canvas_w)
        y = _clamp_map_value(_coord_value(coordinates, "y", canvas_h / 2), canvas_h / 2, 0.0, canvas_h)
    elif region:
        slot = index % 9
        x = _clamp_map_value(
            region.get("x", canvas_w * 0.08) + canvas_w * 0.05 + (slot % 3) * max(canvas_w * 0.05, float(region.get("width", 25)) / 3),
            canvas_w / 2,
            0.0,
            canvas_w,
        )
        y = _clamp_map_value(
            region.get("y", canvas_h * 0.08) + canvas_h * 0.06 + (slot // 3) * max(canvas_h * 0.05, float(region.get("height", 18)) / 3),
            canvas_h / 2,
            0.0,
            canvas_h,
        )
    else:
        x = _clamp_map_value(canvas_w * 0.12 + (index % 8) * canvas_w * 0.1, canvas_w / 2, 0.0, canvas_w)
        y = _clamp_map_value(canvas_h * 0.18 + (index // 8) * canvas_h * 0.12, canvas_h / 2, 0.0, canvas_h)
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
        "icon": coordinates.get("icon") or traits.get("icon"),
        "asset_id": coordinates.get("asset_id") or traits.get("asset_id"),
        "asset_url": coordinates.get("asset_url") or coordinates.get("image") or traits.get("asset_url"),
        "color": coordinates.get("color") or traits.get("color"),
        "size": _clamp_map_value(coordinates.get("size"), 1.0, 0.4, 3.0),
        "label_position": coordinates.get("label_position") or "right",
        "explicit_position": explicit,
        "source": "place",
    }


def _route_points(raw_points: Any, canvas: dict[str, Any]) -> list[dict[str, float]]:
    """把路线点规整到地图画布坐标系。

    输入来自 profile.rules.map.routes 或 world_routes.points；输出是可绘制点列表。
    支持 [[x,y], ...] 和 {"x":..., "y":...} 两种形态，坏点跳过。
    """
    points: list[dict[str, float]] = []
    canvas_w = float(canvas.get("width") or _DEFAULT_MAP_CANVAS["width"])
    canvas_h = float(canvas.get("height") or _DEFAULT_MAP_CANVAS["height"])
    for point in raw_points or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        elif isinstance(point, dict):
            x, y = point.get("x"), point.get("y")
        else:
            continue
        points.append({
            "x": _clamp_map_value(x, 0.0, 0.0, canvas_w),
            "y": _clamp_map_value(y, 0.0, 0.0, canvas_h),
        })
    return points


def _scope_map_point(scope_kind: str | None, scope_id: str | None,
                     region_shapes: dict[str, dict[str, Any]],
                     markers: dict[str, dict[str, Any]]) -> dict[str, float] | None:
    """把 region/place 作用域解析为地图点。

    输入是 route/condition 的结构化 scope；输出是区域中心点或地点 marker 点。找不到
    时返回 None，调用方会保留记录但不强行猜测。
    """
    kind = str(scope_kind or "").strip()
    sid = str(scope_id or "").strip()
    if not sid:
        return None
    if kind == "place" and sid in markers:
        marker = markers[sid]
        return {"x": float(marker.get("x") or 0), "y": float(marker.get("y") or 0)}
    if kind == "region" and sid in region_shapes:
        region = region_shapes[sid]
        return {
            "x": float(region.get("x") or 0) + float(region.get("width") or 0) / 2,
            "y": float(region.get("y") or 0) + float(region.get("height") or 0) / 2,
        }
    return None


def _map_routes(map_cfg: dict[str, Any], canvas: dict[str, Any],
                route_records: list[dict[str, Any]] | None = None,
                region_shapes: dict[str, dict[str, Any]] | None = None,
                markers: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """生成地图路线/道路结构。

    输入兼容 profile.rules.map.routes，并合并一等 world_routes 记录。输出为可绘制
    折线；一等路线如果没有显式 points，会尝试从 from/to scope 的 region/place 坐标
    推导。非法或不足两个点的路线会被跳过。
    """
    routes: list[dict[str, Any]] = []
    for idx, route in enumerate(_iter_map_records(map_cfg.get("routes") or map_cfg.get("paths"))):
        points = _route_points(route.get("points"), canvas)
        if len(points) < 2:
            continue
        role = str(route.get("role") or route.get("route_type") or "road")
        routes.append({
            "id": route.get("id") or route.get("key") or f"route.{idx}",
            "name": route.get("name") or route.get("label") or "路线",
            "role": role,
            "points": points,
            "width": _clamp_map_value(route.get("width"), 1.0, 0.2, 8.0),
            "color": route.get("color"),
            "dash": route.get("dash"),
            "source": "profile.rules.map.routes",
        })
    region_shapes = region_shapes or {}
    markers = markers or {}
    for idx, route in enumerate(route_records or []):
        traits = route.get("traits") if isinstance(route.get("traits"), dict) else {}
        points = _route_points(route.get("points"), canvas)
        if len(points) < 2:
            start = _scope_map_point(route.get("from_scope_kind"), route.get("from_scope_id"), region_shapes, markers)
            end = _scope_map_point(route.get("to_scope_kind"), route.get("to_scope_id"), region_shapes, markers)
            points = [p for p in (start, end) if p]
        if len(points) < 2:
            continue
        role = str(route.get("route_type") or traits.get("role") or "road")
        routes.append({
            "id": route.get("id") or route.get("key") or f"world_route.{idx}",
            "key": route.get("key"),
            "name": route.get("name") or route.get("key") or "路线",
            "role": role,
            "points": points,
            "width": _clamp_map_value(traits.get("width"), 1.2, 0.2, 8.0),
            "color": traits.get("color"),
            "dash": traits.get("dash") or ("3 2" if route.get("status") == "blocked" else None),
            "status": route.get("status"),
            "travel_mode": route.get("travel_mode"),
            "duration_minutes": route.get("duration_minutes"),
            "distance_value": route.get("distance_value"),
            "distance_unit": route.get("distance_unit"),
            "risk_level": route.get("risk_level"),
            "from_scope_kind": route.get("from_scope_kind"),
            "from_scope_id": route.get("from_scope_id"),
            "to_scope_kind": route.get("to_scope_kind"),
            "to_scope_id": route.get("to_scope_id"),
            "source": "world_routes",
        })
    return routes


def _map_conditions(conditions: list[dict[str, Any]] | None,
                    region_shapes: dict[str, dict[str, Any]],
                    markers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """把动态世界状态投影到地图作用域。

    输入是 active conditions；输出保留原状态字段，并在 region/place 可定位时补充 x/y。
    WebUI 可以用列表展示，未来也能直接叠加危险区、机会点和流言热度。
    """
    out: list[dict[str, Any]] = []
    for condition in conditions or []:
        point = _scope_map_point(condition.get("scope_kind"), condition.get("scope_id"), region_shapes, markers)
        item = {
            "id": condition.get("id"),
            "key": condition.get("key"),
            "title": condition.get("title"),
            "condition_type": condition.get("condition_type"),
            "scope_kind": condition.get("scope_kind"),
            "scope_id": condition.get("scope_id"),
            "severity": condition.get("severity"),
            "intensity": condition.get("intensity"),
            "status": condition.get("status"),
            "starts_at": condition.get("starts_at"),
            "ends_at": condition.get("ends_at"),
            "summary": condition.get("summary"),
            "source": "world_conditions",
        }
        if point:
            item.update(point)
            item["located"] = True
        else:
            item["located"] = False
        out.append(item)
    return out


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
              routes: list[dict[str, Any]] | None = None,
              conditions: list[dict[str, Any]] | None = None,
              *, current_location: dict[str, Any] | None = None,
              actor_label: str | None = "明灯") -> dict[str, Any]:
    """生成结构化世界地图。

    输入是已解码的世界档案、区域、地点，以及可选当前 location；输出是 WebUI 和
    context 可共享的地图对象，包括画布、视口能力、网格、图片资源、图片图层、地形
    层、路线、区域形状、地点/建筑标记、动态状态和明灯当前位置标记。函数不写
    数据库；地形、坐标、图片引用和交互能力来自结构字段：
    profile.rules.map、region.traits.map、place.coordinates，以及可选的一等
    world_routes/world_conditions 记录。
    """
    profile_rules = profiles[0].get("rules") if profiles and isinstance(profiles[0].get("rules"), dict) else {}
    map_cfg = _nested_map_config(profile_rules)
    canvas = _map_canvas(map_cfg)
    viewport = _map_viewport(map_cfg, canvas)
    grid = _map_grid(map_cfg, canvas)
    assets = _map_assets(map_cfg)
    image_layers = _map_image_layers(map_cfg, assets, canvas)
    terrain_layers: list[dict[str, Any]] = []
    for idx, layer in enumerate(map_cfg.get("terrain_layers") or []):
        if not isinstance(layer, dict):
            continue
        terrain = str(layer.get("terrain") or layer.get("key") or "custom")
        rect = _rect_from_map_record(layer, canvas)
        terrain_layers.append({
            "id": layer.get("id") or layer.get("key") or f"terrain.{idx}",
            "name": layer.get("name") or layer.get("label") or _TERRAIN_LABELS.get(terrain, terrain),
            "terrain": terrain,
            "terrain_label": _TERRAIN_LABELS.get(terrain, terrain),
            **rect,
            "source": "profile.rules.map.terrain_layers",
        })

    region_shapes = [_region_shape(region, i, len(regions), canvas) for i, region in enumerate(regions)]
    region_shape_by_id = {str(r.get("id")): r for r in region_shapes if r.get("id")}
    markers = [_marker_from_place(place, i, region_shape_by_id, canvas) for i, place in enumerate(places)]
    marker_by_id = {str(m.get("id")): m for m in markers if m.get("id")}
    map_routes = _map_routes(map_cfg, canvas, routes, region_shape_by_id, marker_by_id)
    map_conditions = _map_conditions(conditions, region_shape_by_id, marker_by_id)
    terrains = terrain_layers + region_shapes
    actor = _actor_marker(markers, current_location, actor_label)
    marker_roles = {"place": _MARKER_ROLE_LABELS["place"], "important_building": _MARKER_ROLE_LABELS["important_building"]}
    for marker in markers:
        role = marker.get("marker_role") or "place"
        marker_roles[role] = _MARKER_ROLE_LABELS.get(role, role)
    return {
        "canvas": canvas,
        "viewport": viewport,
        "grid": grid,
        "assets": assets,
        "image_layers": image_layers,
        "terrain": terrains,
        "routes": map_routes,
        "conditions": map_conditions,
        "regions": region_shapes,
        "markers": markers,
        "actor": actor,
        "legend": {
            "terrain": [
                {"terrain": terrain, "label": label}
                for terrain, label in sorted({t.get("terrain"): t.get("terrain_label") for t in terrains}.items())
            ],
            "marker_roles": marker_roles,
        },
        "counts": {
            "terrain": len(terrains),
            "image_layers": len(image_layers),
            "routes": len(map_routes),
            "conditions": len(map_conditions),
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
    "route": ("world_routes", _decode_route),
    "condition": ("world_conditions", _decode_condition),
    "chronicle_event": ("world_chronicle_events", _decode_chronicle_event),
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


def _validate_route_endpoint(conn, owner_kind: str, owner_id: str,
                             scope_kind: str | None, scope_id: str | None) -> tuple[str | None, str | None]:
    """校验路线端点。

    输入来自 world_routes 的 from/to scope；输出规整后的端点。端点可以为空，表示纯
    地图折线；一旦给出端点，就必须指向现有 world/region/place，避免路线引用漂移。
    """
    if not scope_kind and not scope_id:
        return None, None
    if not scope_kind or not scope_id:
        raise ValueError("route endpoint requires both scope_kind and scope_id")
    return _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)


def upsert_route(conn, owner_kind: str, owner_id: str, *, key: str, name: str,
                 route_type: str = "road",
                 from_scope_kind: str | None = None, from_scope_id: str | None = None,
                 to_scope_kind: str | None = None, to_scope_id: str | None = None,
                 travel_mode: str | None = None,
                 distance_value: float | None = None, distance_unit: str | None = None,
                 duration_minutes: float | None = None, risk_level: float = 0.0,
                 cost: dict[str, Any] | None = None,
                 schedule: dict[str, Any] | None = None,
                 points: list[Any] | None = None,
                 traits: dict[str, Any] | None = None,
                 evidence: dict[str, Any] | None = None,
                 status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新一条世界路线/交通边。

    输入是稳定 key、端点、地图折线和耗时/风险等结构字段；输出 route 记录。路线是
    玩法层基础设施，供地图、外勤估算和区域封锁读取；具体交通规则仍由世界观解释。
    """
    key = _clean_key(key)
    name = str(name or "").strip()
    if not name:
        raise ValueError("route name is required")
    if status not in {"active", "blocked", "closed", "archived"}:
        raise ValueError("route status must be active/blocked/closed/archived")
    from_kind, from_id = _validate_route_endpoint(conn, owner_kind, owner_id, from_scope_kind, from_scope_id)
    to_kind, to_id = _validate_route_endpoint(conn, owner_kind, owner_id, to_scope_kind, to_scope_id)
    risk = _clamp_map_value(risk_level, 0.0, 0.0, 100.0)
    existing = conn.execute(
        "SELECT id FROM world_routes WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        route_id = existing["id"]
        conn.execute(
            """UPDATE world_routes
               SET name=?, route_type=?, from_scope_kind=?, from_scope_id=?,
                   to_scope_kind=?, to_scope_id=?, travel_mode=?, distance_value=?,
                   distance_unit=?, duration_minutes=?, risk_level=?, cost_json=?,
                   schedule_json=?, points_json=?, traits_json=?, evidence_json=?,
                   status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (name, route_type or "road", from_kind, from_id, to_kind, to_id,
             travel_mode, distance_value, distance_unit, duration_minutes, risk,
             dumps(cost or {}), dumps(schedule or {}), dumps(points or []),
             dumps(traits or {}), dumps(evidence or {}), status, source, route_id),
        )
        event_type = "world_route_updated"
    else:
        route_id = new_id("worldroute")
        conn.execute(
            """INSERT INTO world_routes(
                 id, owner_kind, owner_id, key, name, route_type, from_scope_kind,
                 from_scope_id, to_scope_kind, to_scope_id, travel_mode,
                 distance_value, distance_unit, duration_minutes, risk_level,
                 cost_json, schedule_json, points_json, traits_json, evidence_json,
                 status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (route_id, owner_kind, owner_id, key, name, route_type or "road",
             from_kind, from_id, to_kind, to_id, travel_mode, distance_value,
             distance_unit, duration_minutes, risk, dumps(cost or {}),
             dumps(schedule or {}), dumps(points or []), dumps(traits or {}),
             dumps(evidence or {}), status, source),
        )
        event_type = "world_route_created"
    append_journal(conn, owner_kind, owner_id, event_type,
                   {"route_id": route_id, "key": key, "from_scope_id": from_id, "to_scope_id": to_id}, source)
    return _decode_route(conn.execute("SELECT * FROM world_routes WHERE id=?", (route_id,)).fetchone())


def upsert_condition(conn, owner_kind: str, owner_id: str, *, key: str, title: str,
                     condition_type: str = "state", scope_kind: str = "world",
                     scope_id: str | None = None, severity: float = 0.0,
                     intensity: float = 0.0, summary: str | None = None,
                     content: str | None = None, starts_at: str | None = None,
                     ends_at: str | None = None,
                     payload: dict[str, Any] | None = None,
                     evidence: dict[str, Any] | None = None,
                     status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新动态世界状态。

    输入是作用域、强度、时间窗和扩展 payload；输出 condition 记录。它承载灵压、
    人流、危险、机会、流言热度等玩法状态，但核心不解释这些世界观含义。
    """
    key = _clean_key(key)
    title = str(title or "").strip()
    if not title:
        raise ValueError("condition title is required")
    if status not in {"active", "resolved", "expired", "archived"}:
        raise ValueError("condition status must be active/resolved/expired/archived")
    scope_kind, scope_id = _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)
    severity_v = _clamp_map_value(severity, 0.0, 0.0, 100.0)
    intensity_v = _clamp_map_value(intensity, 0.0, 0.0, 100.0)
    existing = conn.execute(
        "SELECT id FROM world_conditions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        condition_id = existing["id"]
        conn.execute(
            """UPDATE world_conditions
               SET title=?, condition_type=?, scope_kind=?, scope_id=?, severity=?,
                   intensity=?, summary=?, content=?, starts_at=?, ends_at=?,
                   payload_json=?, evidence_json=?, status=?, source=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (title, condition_type or "state", scope_kind, scope_id, severity_v,
             intensity_v, summary, content, starts_at, ends_at, dumps(payload or {}),
             dumps(evidence or {}), status, source, condition_id),
        )
        event_type = "world_condition_updated"
    else:
        condition_id = new_id("worldcond")
        conn.execute(
            """INSERT INTO world_conditions(
                 id, owner_kind, owner_id, key, title, condition_type, scope_kind,
                 scope_id, severity, intensity, summary, content, starts_at, ends_at,
                 payload_json, evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (condition_id, owner_kind, owner_id, key, title, condition_type or "state",
             scope_kind, scope_id, severity_v, intensity_v, summary, content,
             starts_at, ends_at, dumps(payload or {}), dumps(evidence or {}),
             status, source),
        )
        event_type = "world_condition_created"
    append_journal(conn, owner_kind, owner_id, event_type,
                   {"condition_id": condition_id, "key": key, "scope_kind": scope_kind, "scope_id": scope_id, "status": status}, source)
    return _decode_condition(conn.execute("SELECT * FROM world_conditions WHERE id=?", (condition_id,)).fetchone())


def upsert_chronicle_event(conn, owner_kind: str, owner_id: str, *, key: str, title: str,
                           event_type: str = "milestone", era_key: str | None = None,
                           expansion_key: str | None = None, campaign_id: str | None = None,
                           scope_kind: str = "world", scope_id: str | None = None,
                           occurred_at: str | None = None, sort_order: float = 0.0,
                           summary: str | None = None, content: str | None = None,
                           tags: list[str] | None = None,
                           related: dict[str, Any] | None = None,
                           evidence: dict[str, Any] | None = None,
                           status: str = "active", source: str = "life_world") -> dict[str, Any]:
    """创建或更新一条世界大事记/编年史事件。

    输入来自 life_world 的 chronicle_event/upsert_chronicle_event 写操作；输出是
    持久化史事记录。它用于记录系统上线前背景、资料片更新、世界大事件和地点史，
    生命周期随 owner 本地 DB 持久化。正文 content 可展开阅读，但生效范围由
    scope_kind/scope_id 控制；资料片联动按 expansion_key/campaign_id 幂等维护。
    """
    key = _clean_key(key)
    title = str(title or "").strip()
    if not title:
        raise ValueError("chronicle event title is required")
    if status not in {"active", "archived"}:
        raise ValueError("chronicle event status must be active/archived")
    scope_kind, scope_id = _validate_scope(conn, owner_kind, owner_id, scope_kind, scope_id)
    try:
        sort_value = float(sort_order or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("chronicle event sort_order must be numeric") from exc
    existing = conn.execute(
        "SELECT id FROM world_chronicle_events WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if existing:
        event_id = existing["id"]
        conn.execute(
            """UPDATE world_chronicle_events
               SET title=?, event_type=?, era_key=?, expansion_key=?, campaign_id=?,
                   scope_kind=?, scope_id=?, occurred_at=?, sort_order=?, summary=?,
                   content=?, tags_json=?, related_json=?, evidence_json=?,
                   status=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (title, event_type or "milestone", era_key, expansion_key, campaign_id,
             scope_kind, scope_id, occurred_at, sort_value, summary, content,
             dumps(tags or []), dumps(related or {}), dumps(evidence or {}),
             status, source, event_id),
        )
        event_kind = "world_chronicle_event_updated"
    else:
        event_id = new_id("worldchron")
        conn.execute(
            """INSERT INTO world_chronicle_events(
                 id, owner_kind, owner_id, key, title, event_type, era_key,
                 expansion_key, campaign_id, scope_kind, scope_id, occurred_at,
                 sort_order, summary, content, tags_json, related_json,
                 evidence_json, status, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, owner_kind, owner_id, key, title, event_type or "milestone",
             era_key, expansion_key, campaign_id, scope_kind, scope_id, occurred_at,
             sort_value, summary, content, dumps(tags or []), dumps(related or {}),
             dumps(evidence or {}), status, source),
        )
        event_kind = "world_chronicle_event_created"
    append_journal(conn, owner_kind, owner_id, event_kind,
                   {"chronicle_event_id": event_id, "key": key, "scope_kind": scope_kind,
                    "scope_id": scope_id, "expansion_key": expansion_key}, source)
    return _decode_chronicle_event(conn.execute("SELECT * FROM world_chronicle_events WHERE id=?", (event_id,)).fetchone())


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
            ("condition", "world_conditions", None),
            ("chronicle_event", "world_chronicle_events", None),
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
        route_rows = conn.execute(
            """SELECT id FROM world_routes
               WHERE owner_kind=? AND owner_id=? AND status!='archived'
                 AND ((from_scope_kind='region' AND from_scope_id=?)
                   OR (to_scope_kind='region' AND to_scope_id=?))""",
            (owner_kind, owner_id, object_id, object_id),
        ).fetchall()
        deps.extend({"kind": "route", "id": r["id"]} for r in route_rows)
    elif kind == "place":
        checks = [
            ("place", "world_places", "parent_place_id"),
            ("lore", "world_lore_entries", None),
            ("faction_presence", "world_faction_presence", None),
            ("condition", "world_conditions", None),
            ("chronicle_event", "world_chronicle_events", None),
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
        route_rows = conn.execute(
            """SELECT id FROM world_routes
               WHERE owner_kind=? AND owner_id=? AND status!='archived'
                 AND ((from_scope_kind='place' AND from_scope_id=?)
                   OR (to_scope_kind='place' AND to_scope_id=?))""",
            (owner_kind, owner_id, object_id, object_id),
        ).fetchall()
        deps.extend({"kind": "route", "id": r["id"]} for r in route_rows)
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
        raise ValueError("object_kind must be profile/region/place/lore/faction_presence/route/condition/chronicle_event")
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


def list_routes(conn, owner_kind: str, owner_id: str, *,
                scope_kind: str | None = None, scope_id: str | None = None,
                status: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
    """列出世界路线，可按端点 scope 过滤。

    默认返回非 archived 路线，包括 active 和 blocked，供地图和外勤估算知道道路是否
    暂时不可通行。传入 status 时按精确状态过滤。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if scope_kind and scope_id:
        where += " AND ((from_scope_kind=? AND from_scope_id=?) OR (to_scope_kind=? AND to_scope_id=?))"
        params.extend([scope_kind, scope_id, scope_kind, scope_id])
    if status:
        where += " AND status=?"
        params.append(status)
    else:
        where += " AND status!='archived'"
    rows = conn.execute(
        f"SELECT * FROM world_routes {where} ORDER BY risk_level DESC, updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_route(r) for r in rows]


def list_conditions(conn, owner_kind: str, owner_id: str, *,
                    scope_kind: str | None = None, scope_id: str | None = None,
                    condition_type: str | None = None,
                    status: str | None = "active", limit: int = 80) -> list[dict[str, Any]]:
    """列出动态世界状态，可按结构作用域过滤。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if scope_kind:
        where += " AND scope_kind=?"
        params.append(scope_kind)
    if scope_id:
        where += " AND scope_id=?"
        params.append(scope_id)
    if condition_type:
        where += " AND condition_type=?"
        params.append(condition_type)
    if status:
        where += " AND status=?"
        params.append(status)
    else:
        where += " AND status!='archived'"
    rows = conn.execute(
        f"SELECT * FROM world_conditions {where} ORDER BY severity DESC, updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_condition(r) for r in rows]


def list_chronicle_events(conn, owner_kind: str, owner_id: str, *,
                          scope_kind: str | None = None, scope_id: str | None = None,
                          event_type: str | None = None, era_key: str | None = None,
                          expansion_key: str | None = None, campaign_id: str | None = None,
                          status: str | None = "active", limit: int = 120) -> list[dict[str, Any]]:
    """列出世界大事记/编年史事件。

    输入可按作用域、纪元、资料片或 campaign 过滤；输出按人工排序和世界内时间升序
    返回。调用方是 life_world 读操作、effective_context 和 WebUI。函数只读数据库，
    不把历史事件解释成现实事件，也不自动回填旧设定。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if scope_kind:
        where += " AND scope_kind=?"
        params.append(scope_kind)
    if scope_id:
        where += " AND scope_id=?"
        params.append(scope_id)
    if event_type:
        where += " AND event_type=?"
        params.append(event_type)
    if era_key:
        where += " AND era_key=?"
        params.append(era_key)
    if expansion_key:
        where += " AND expansion_key=?"
        params.append(expansion_key)
    if campaign_id:
        where += " AND campaign_id=?"
        params.append(campaign_id)
    if status:
        where += " AND status=?"
        params.append(status)
    else:
        where += " AND status!='archived'"
    rows = conn.execute(
        f"""SELECT * FROM world_chronicle_events {where}
            ORDER BY sort_order ASC, COALESCE(occurred_at, '') ASC, created_at ASC
            LIMIT ?""",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_chronicle_event(r) for r in rows]


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
    conditions = _collect_effective_entries(conn, owner_kind, owner_id, effective_scopes, list_conditions, limit_v)
    chronicle_events = _collect_effective_entries(conn, owner_kind, owner_id, effective_scopes, list_chronicle_events, limit_v)
    route_by_id: dict[str, dict[str, Any]] = {}
    for scope_kind, scope_id in effective_scopes:
        for route in list_routes(conn, owner_kind, owner_id, scope_kind=scope_kind, scope_id=scope_id, limit=limit_v):
            if route.get("id"):
                route_by_id[str(route["id"])] = route

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
        "routes": list(route_by_id.values())[:limit_v],
        "conditions": conditions,
        "chronicle_events": chronicle_events,
        "counts": {
            "profiles": len(profiles),
            "regions": 1 if region else 0,
            "places": 1 if place else 0,
            "lore": len(lore),
            "faction_presence": len(presence),
            "routes": min(len(route_by_id), limit_v),
            "conditions": len(conditions),
            "chronicle_events": len(chronicle_events),
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
    routes = list_routes(conn, owner_kind, owner_id, limit=limit)
    conditions = list_conditions(conn, owner_kind, owner_id, limit=limit)
    chronicle_events = list_chronicle_events(conn, owner_kind, owner_id, limit=max(limit, 40))
    return {
        "profiles": profiles,
        "regions": regions,
        "places": places,
        "lore": lore,
        "faction_presence": presence,
        "routes": routes,
        "conditions": conditions,
        "chronicle_events": chronicle_events,
        "map": map_state(profiles, regions, places, routes, conditions),
        "counts": {
            "profiles": len(profiles),
            "regions": len(regions),
            "places": len(places),
            "lore": len(lore),
            "faction_presence": len(presence),
            "routes": len(routes),
            "conditions": len(conditions),
            "chronicle_events": len(chronicle_events),
        },
    }
