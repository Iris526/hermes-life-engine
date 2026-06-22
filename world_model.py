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


_ARCHIVE_TABLES = {
    "profile": ("world_profiles", _decode_profile),
    "region": ("world_regions", _decode_region),
    "place": ("world_places", _decode_place),
    "lore": ("world_lore_entries", _decode_lore),
    "faction_presence": ("world_faction_presence", _decode_presence),
}


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


def archive_object(conn, owner_kind: str, owner_id: str, *, object_kind: str,
                   object_id: str | None = None, key: str | None = None,
                   faction_entity_id: str | None = None, scope_kind: str | None = None,
                   scope_id: str | None = None, source: str = "life_world") -> dict[str, Any]:
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
    conn.execute(f"UPDATE {table} SET status='archived', updated_at=datetime('now') WHERE id=?", (row["id"],))
    append_journal(conn, owner_kind, owner_id, f"world_{kind}_archived", {"object_kind": kind, "object_id": row["id"]}, source)
    return decoder(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone())


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
    loc = location if isinstance(location, dict) else {}
    rid = region_id or loc.get("world_region_id") or loc.get("region_id")
    rkey = region_key or loc.get("world_region_key") or loc.get("region_key")
    pid = place_id or loc.get("world_place_id") or loc.get("place_id")
    pkey = place_key or loc.get("world_place_key") or loc.get("place_key")
    place = _lookup_place(conn, owner_kind, owner_id, place_id=pid, place_key=pkey)
    if place and not rid and not rkey:
        rid = place.get("region_id")
    region = _lookup_region(conn, owner_kind, owner_id, region_id=rid, region_key=rkey)

    scopes: list[tuple[str, str]] = [("world", WORLD_SCOPE_ID)]
    if region.get("id"):
        scopes.append(("region", region["id"]))
    if place.get("id"):
        scopes.append(("place", place["id"]))

    lore: list[dict[str, Any]] = []
    presence: list[dict[str, Any]] = []
    per_scope_limit = max(1, int(limit))
    for scope_kind, scope_id in scopes:
        lore.extend(list_lore_entries(
            conn, owner_kind, owner_id,
            scope_kind=scope_kind, scope_id=scope_id,
            limit=per_scope_limit,
        ))
        presence.extend(list_faction_presence(
            conn, owner_kind, owner_id,
            scope_kind=scope_kind, scope_id=scope_id,
            limit=per_scope_limit,
        ))

    profiles = list_profiles(conn, owner_kind, owner_id, limit=2)
    return {
        "activation": {
            "scope_ids": [{"scope_kind": kind, "scope_id": sid} for kind, sid in scopes],
            "region_id": region.get("id"),
            "region_key": region.get("key"),
            "place_id": place.get("id"),
            "place_key": place.get("key"),
        },
        "profiles": profiles,
        "regions": [region] if region else [],
        "places": [place] if place else [],
        "lore": lore[:int(limit)],
        "faction_presence": presence[:int(limit)],
        "counts": {
            "profiles": len(profiles),
            "regions": 1 if region else 0,
            "places": 1 if place else 0,
            "lore": len(lore[:int(limit)]),
            "faction_presence": len(presence[:int(limit)]),
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
        "counts": {
            "profiles": len(profiles),
            "regions": len(regions),
            "places": len(places),
            "lore": len(lore),
            "faction_presence": len(presence),
        },
    }
