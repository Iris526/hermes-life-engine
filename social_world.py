"""世界观社会层的能力槽与账本。

本模块只定义 LifeEngine 的社会世界底座：实体、归属、关系边、声望账本、
评价和流言。具体世界观里的“门派 / 公司 / 学院 / 神族”、声望维度和流言
渠道都来自 Canon 或 `worldview_slot_definitions`，这里不写死任何设定。
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id

WORLD_AUDIENCE = "__world__"

_SLOT_CANON_KEYS = {
    "entity_kind": "entity_kinds",
    "relationship_axis": "relationship_axes",
    "reputation_axis": "reputation_axes",
    "evaluation_axis": "evaluation_axes",
    "rumor_channel": "rumor_channels",
    "request_type": "request_types",
}

_REQUEST_TERMINAL_STATUSES = {"rejected", "completed", "expired", "cancelled"}
_REQUEST_STATUSES = {"open", "accepted", "in_progress", "rejected", "completed", "expired", "cancelled"}


def _clamp_float(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _row(row) -> dict[str, Any]:
    return dict(row) if row else {}


def _decode_json_fields(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    for field in fields:
        if field in item:
            item[field[:-5] if field.endswith("_json") else field] = loads(item.pop(field), {})
    return item


def _decode_slot(row) -> dict[str, Any]:
    out = _decode_json_fields(_row(row), ["config_json"])
    if out:
        out.setdefault("origin", "db")
    return out


def _decode_entity(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["traits_json", "metadata_json"])


def _decode_affiliation(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["evidence_json"])


def _decode_edge(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["evidence_json"])


def _decode_evaluation(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["evidence_json"])


def _decode_request(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["details_json", "quote_json", "billing_json", "evidence_json"])


def _decode_request_transition(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["quote_json", "billing_json", "evidence_json"])


def _decode_reputation_event(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["evidence_json"])


def _decode_rumor(row) -> dict[str, Any]:
    return _decode_json_fields(_row(row), ["evidence_json"])


def _slot_entries_from_value(slot_type: str, value: Any) -> list[dict[str, Any]]:
    """把 Canon 里的槽定义规整成统一记录。

    输入来自 `canon["worldview"]["social_slots"]`，允许 dict/list/字符串等宽松
    形态；输出是只读槽定义列表。调用方是 `slot_catalog` 和测试。函数不写库，
    失败时跳过坏项，保证具体世界观包可以渐进补齐。
    """
    entries: list[dict[str, Any]] = []
    if isinstance(value, dict):
        iterable = value.items()
    elif isinstance(value, list):
        iterable = []
        for item in value:
            if isinstance(item, dict):
                key = item.get("key") or item.get("name") or item.get("id")
                iterable.append((key, item))
            else:
                iterable.append((item, {"label": str(item)}))
    else:
        iterable = []
    for key, raw in iterable:
        k = str(key or "").strip()
        if not k:
            continue
        if isinstance(raw, dict):
            label = raw.get("label") or raw.get("display_name") or raw.get("name") or k
            description = raw.get("description")
            config = {kk: vv for kk, vv in raw.items() if kk not in {"key", "id", "name", "label", "display_name", "description"}}
        else:
            label = str(raw or k)
            description = None
            config = {}
        entries.append({
            "id": f"canon:{slot_type}:{k}",
            "slot_type": slot_type,
            "key": k,
            "label": str(label),
            "description": description,
            "config": config,
            "status": "active",
            "source": "canon_worldview",
            "origin": "canon",
        })
    return entries


_GUIMINGGUAN_DEFAULT_SOCIAL_SLOTS = {
    "entity_kind": {
        "shrine": ("道观/宫观", "供香客、常客与委托人形成社会关系的场所或经营主体。"),
        "agent": ("生活主体", "当前 LifeEngine 主体在社会世界中的实体。"),
        "visitor_group": ("访客群体", "香客、常客或本地顾客等群体实体。"),
        "client": ("委托人", "提出上门、外勤或勘察需求的个人或未具名委托实体。"),
        "patron": ("香客/主顾", "持续来访、供奉或购买服务的人。"),
        "merchant": ("商户", "商业圈层或商户身份。"),
        "neighborhood": ("本地圈层", "邻里、街坊、商户圈等非地图枚举的社会圈层。"),
        "venue": ("场所", "可被事件 freeform location 指向的地点实体。"),
    },
    "relationship_axis": {
        "trust": ("信任", "一方对另一方可靠性的判断。"),
        "familiarity": ("熟悉", "重复接触积累的熟悉度。"),
        "gratitude": ("感谢", "因帮助、服务或交付产生的感谢。"),
        "suspicion": ("怀疑", "失败、延期或不透明带来的疑虑。"),
        "obligation": ("人情/义务", "未结清的人情、承诺或后续责任。"),
    },
    "reputation_axis": {
        "trustworthy": ("可信", "在相关 audience 中被认为可靠可信。"),
        "approachable": ("亲近可问", "让人愿意上门、询问或求助。"),
        "efficacious": ("灵验/有效", "服务、符箓或处理结果被认为有效。"),
        "fieldwork_reliability": ("外勤可靠", "上门、勘察、处理委托时的稳定交付。"),
        "price_fairness": ("价钱公道", "价格是否被认为合理。"),
    },
    "evaluation_axis": {
        "satisfaction": ("满意度", "评价者对服务或结果的满意度。"),
        "professionalism": ("专业度", "处理过程是否显得专业、有章法。"),
        "kindness": ("待人温和", "待人是否温和、愿意解释。"),
        "perceived_effectiveness": ("感知效果", "评价者感知到的效果。"),
        "price_acceptance": ("价格接受度", "评价者是否接受价格。"),
    },
    "rumor_channel": {
        "visitor_word_of_mouth": ("香客口碑", "香客、常客之间的低热度口碑。"),
        "east_market_gossip": ("东市闲谈", "东市或相近商业环境中的闲谈渠道；不是地图枚举。"),
        "commission_backchannel": ("委托人私下反馈", "委托人与中间人之间的私下评价。"),
        "neighborhood_talk": ("邻里闲话", "本地圈层里的低热度传播。"),
    },
}


def ensure_default_guimingguan_social_slots(conn, owner_kind: str, owner_id: str,
                                           source: str = "social_projector") -> list[dict[str, Any]]:
    """Ensure the generic slots used by the Guimingguan social projector exist.

    These slots describe social primitives only. They intentionally do not
    declare concrete origin, faction, or map-location enums; generated entities
    keep those attributes as unknown/freeform/pending metadata until a worldview
    package defines the relevant slots.
    """
    out: list[dict[str, Any]] = []
    existing = {
        (row["slot_type"], row["key"])
        for row in conn.execute(
            "SELECT slot_type, key FROM worldview_slot_definitions WHERE owner_kind=? AND owner_id=? AND status='active'",
            (owner_kind, owner_id),
        ).fetchall()
    }
    for slot_type, entries in _GUIMINGGUAN_DEFAULT_SOCIAL_SLOTS.items():
        for key, (label, description) in entries.items():
            if (slot_type, key) in existing:
                continue
            out.append(upsert_slot_definition(
                conn, owner_kind, owner_id,
                slot_type=slot_type,
                key=key,
                label=label,
                description=description,
                config={"default_for": "guimingguan_social_projection"},
                source=source,
            ))
    return out


def slots_from_canon(canon: dict[str, Any] | None) -> list[dict[str, Any]]:
    """读取当前世界观 Canon 暴露的社会能力槽。

    输入是 `get_active_canon()` 的结果；输出包含 entity kind、relationship axis、
    reputation axis、evaluation axis 和 rumor channel。它只是解释配置，不创建表
    记录。调用方是 `life_social slots/summary`，用于让具体世界观包决定槽位含义。
    """
    worldview = (canon or {}).get("worldview") or {}
    slots = worldview.get("social_slots") or {}
    out: list[dict[str, Any]] = []
    if isinstance(slots, dict):
        for slot_type, canon_key in _SLOT_CANON_KEYS.items():
            out.extend(_slot_entries_from_value(slot_type, slots.get(canon_key)))
    return out


def upsert_slot_definition(conn, owner_kind: str, owner_id: str, *, slot_type: str, key: str,
                           label: str | None = None, description: str | None = None,
                           config: dict[str, Any] | None = None,
                           source: str = "life_social") -> dict[str, Any]:
    """注册或更新一个世界观社会能力槽。

    输入来自 `life_social define_slot` 或 LifeOps；`slot_type` 表示槽类别，`key`
    是世界观包稳定引用名。输出是落库后的定义。副作用是写
    `worldview_slot_definitions` 和 journal。失败时抛出校验错误，外层 LifeOps
    savepoint 会回滚，避免半截世界观定义。
    """
    slot_type = str(slot_type or "").strip()
    key = str(key or "").strip()
    if slot_type not in _SLOT_CANON_KEYS:
        raise ValueError(f"unknown social slot_type: {slot_type}")
    if not key:
        raise ValueError("slot key is required")
    existing = conn.execute(
        "SELECT id FROM worldview_slot_definitions WHERE owner_kind=? AND owner_id=? AND slot_type=? AND key=?",
        (owner_kind, owner_id, slot_type, key),
    ).fetchone()
    if existing:
        slot_id = existing["id"]
        conn.execute(
            """UPDATE worldview_slot_definitions
               SET label=?, description=?, config_json=?, source=?, status='active', updated_at=datetime('now')
               WHERE id=?""",
            (label or key, description, dumps(config or {}), source, slot_id),
        )
    else:
        slot_id = new_id("socialslot")
        conn.execute(
            """INSERT INTO worldview_slot_definitions(
                 id, owner_kind, owner_id, slot_type, key, label, description, config_json, source
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (slot_id, owner_kind, owner_id, slot_type, key, label or key, description, dumps(config or {}), source),
        )
    append_journal(conn, owner_kind, owner_id, "social_slot_defined",
                   {"slot_type": slot_type, "key": key, "slot_id": slot_id}, source)
    return _decode_slot(conn.execute("SELECT * FROM worldview_slot_definitions WHERE id=?", (slot_id,)).fetchone())


def list_slot_definitions(conn, owner_kind: str, owner_id: str, *,
                          slot_type: str | None = None,
                          canon: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """列出当前 owner 可用的社会能力槽。

    输入可以带 `slot_type` 过滤，也可以带 active Canon 以合并 Canon 内声明的槽。
    输出是去重后的槽定义列表，DB 定义优先于 Canon 同名定义。函数只读数据库，供
    工具、上下文构建和测试读取。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=? AND status='active'"
    if slot_type:
        where += " AND slot_type=?"
        params.append(str(slot_type))
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in slots_from_canon(canon):
        if slot_type and item.get("slot_type") != slot_type:
            continue
        merged[(item["slot_type"], item["key"])] = item
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='worldview_slot_definitions'",
    ).fetchone()
    if table_exists:
        rows = conn.execute(
            f"SELECT * FROM worldview_slot_definitions {where} ORDER BY slot_type, key",
            tuple(params),
        ).fetchall()
        for row in rows:
            item = _decode_slot(row)
            merged[(item["slot_type"], item["key"])] = item
    return list(merged.values())


def _slot_keys(conn, owner_kind: str, owner_id: str, slot_type: str,
               canon: dict[str, Any] | None = None) -> set[str]:
    """读取某类槽位的已定义 key。

    输入是槽位类型和可选 Canon；输出用于 advisory validator。该函数只读 DB，不作为
    硬校验来源，因为世界观包可能还在草拟中，写入路径仍允许未知 key。
    """
    return {
        str(item.get("key"))
        for item in list_slot_definitions(conn, owner_kind, owner_id, slot_type=slot_type, canon=canon)
        if item.get("key")
    }


def _collect_used_slot_values(conn, owner_kind: str, owner_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """从社会账本收集正在使用的槽位 key。

    输入是 owner；输出按 `(slot_type, key)` 聚合使用次数和来源表。调用方是
    `slot_advisories`。这里只读取稳定列，不解释具体世界观含义。
    """
    checks = [
        ("entity_kind", "world_entities", "entity_kind", "status!='archived'"),
        ("relationship_axis", "social_edges", "axis", "status='active'"),
        ("reputation_axis", "reputation_accounts", "axis", "status='active'"),
        ("reputation_axis", "reputation_events", "axis", "1=1"),
        ("evaluation_axis", "social_evaluations", "axis", "status='active'"),
        ("rumor_channel", "rumors", "channel", "status!='archived'"),
        ("request_type", "social_requests", "request_type", "status!='archived'"),
    ]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for slot_type, table, column, status_where in checks:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not table_exists:
            continue
        rows = conn.execute(
            f"""SELECT {column} AS key, COUNT(*) AS usage_count
                FROM {table}
                WHERE owner_kind=? AND owner_id=? AND {status_where}
                GROUP BY {column}""",
            (owner_kind, owner_id),
        ).fetchall()
        for row in rows:
            key = str(row["key"] or "").strip()
            if not key:
                continue
            item = out.setdefault(
                (slot_type, key),
                {"slot_type": slot_type, "key": key, "usage_count": 0, "sources": []},
            )
            item["usage_count"] += int(row["usage_count"] or 0)
            item["sources"].append(f"{table}.{column}")
    return out


def slot_advisories(conn, owner_kind: str, owner_id: str, *,
                    canon: dict[str, Any] | None = None,
                    limit: int = 80) -> list[dict[str, Any]]:
    """生成非阻断的社会槽位一致性提示。

    输入是 owner 和可选 Canon；输出 warning/info 列表。它只提示“账本中使用了未定义
    槽位 key”，不会阻止写入，适合世界观仍在扩展时发现 typo、同义词和遗漏定义。
    """
    used = _collect_used_slot_values(conn, owner_kind, owner_id)
    defined_by_type = {
        slot_type: _slot_keys(conn, owner_kind, owner_id, slot_type, canon=canon)
        for slot_type in _SLOT_CANON_KEYS
    }
    out: list[dict[str, Any]] = []
    for (slot_type, key), item in sorted(used.items()):
        if key in defined_by_type.get(slot_type, set()):
            continue
        out.append({
            "severity": "warning",
            "slot_type": slot_type,
            "key": key,
            "usage_count": item["usage_count"],
            "sources": sorted(set(item["sources"])),
            "message": f"{slot_type} '{key}' is used but has no active slot definition",
            "suggested_actions": ["define_slot", "merge_with_existing_slot"],
        })
    return out[:max(1, int(limit))]


def create_entity(conn, owner_kind: str, owner_id: str, *, entity_kind: str,
                  display_name: str, summary: str | None = None,
                  traits: dict[str, Any] | None = None,
                  metadata: dict[str, Any] | None = None,
                  source: str = "life_social") -> dict[str, Any]:
    """创建一个世界观社会实体。

    输入来自工具或 LifeOps，`entity_kind` 只引用世界观槽，不在核心里枚举。输出是
    `world_entities` 行。副作用是写实体表和 journal。实体代表人、势力、地点、
    组织或任何世界观包定义的社会对象，后续关系边、声望和流言都引用它。
    """
    entity_kind = str(entity_kind or "").strip()
    display_name = str(display_name or "").strip()
    if not entity_kind:
        raise ValueError("entity_kind is required")
    if not display_name:
        raise ValueError("display_name is required")
    entity_id = new_id("worldent")
    conn.execute(
        """INSERT INTO world_entities(
             id, owner_kind, owner_id, entity_kind, display_name, summary,
             traits_json, metadata_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (entity_id, owner_kind, owner_id, entity_kind, display_name, summary,
         dumps(traits or {}), dumps(metadata or {}), source),
    )
    append_journal(conn, owner_kind, owner_id, "world_entity_created",
                   {"entity_id": entity_id, "entity_kind": entity_kind, "display_name": display_name}, source)
    return get_entity(conn, entity_id)


def get_entity(conn, entity_id: str) -> dict[str, Any]:
    """读取单个社会实体。

    输入是 `world_entities.id`；输出是解码 JSON 字段后的实体 dict 或空 dict。
    函数只读数据库，供工具、关系/声望写入前校验和测试调用。
    """
    return _decode_entity(conn.execute("SELECT * FROM world_entities WHERE id=?", (entity_id,)).fetchone())


def list_entities(conn, owner_kind: str, owner_id: str, *, entity_kind: str | None = None,
                  status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
    """按 owner 列出社会实体。

    输入可按实体 kind/status 过滤；输出是最近更新的实体列表。函数只读数据库，
    调用方包括 `life_social entities` 和后续上下文注入。
    """
    params: list[Any] = [owner_kind, owner_id, status]
    where = "WHERE owner_kind=? AND owner_id=? AND status=?"
    if entity_kind:
        where += " AND entity_kind=?"
        params.append(str(entity_kind))
    rows = conn.execute(
        f"SELECT * FROM world_entities {where} ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_entity(r) for r in rows]


def link_affiliation(conn, owner_kind: str, owner_id: str, *, subject_entity_id: str,
                     faction_entity_id: str, role: str | None = None,
                     strength: float = 1.0, evidence: dict[str, Any] | None = None,
                     source: str = "life_social") -> dict[str, Any]:
    """记录一个实体对势力/组织/圈层的归属。

    输入是主体实体、承载方实体和可选角色；输出是 affiliation 行。该函数不判断
    `faction_entity_id` 是否真的叫“势力”，它只要求二者是同 owner 下的社会实体，
    具体含义由世界观槽解释。重复归属会更新强度和证据，保持幂等。
    """
    if not _entity_belongs(conn, owner_kind, owner_id, subject_entity_id):
        raise ValueError(f"subject entity not found: {subject_entity_id}")
    if not _entity_belongs(conn, owner_kind, owner_id, faction_entity_id):
        raise ValueError(f"faction entity not found: {faction_entity_id}")
    role = str(role or "member").strip()
    existing = conn.execute(
        """SELECT id FROM world_affiliations
           WHERE owner_kind=? AND owner_id=? AND subject_entity_id=? AND faction_entity_id=? AND role=?""",
        (owner_kind, owner_id, subject_entity_id, faction_entity_id, role),
    ).fetchone()
    if existing:
        affiliation_id = existing["id"]
        conn.execute(
            """UPDATE world_affiliations
               SET strength=?, evidence_json=?, status='active', source=?, updated_at=datetime('now')
               WHERE id=?""",
            (_clamp_float(strength, 0.0, 1.0, 1.0), dumps(evidence or {}), source, affiliation_id),
        )
    else:
        affiliation_id = new_id("affil")
        conn.execute(
            """INSERT INTO world_affiliations(
                 id, owner_kind, owner_id, subject_entity_id, faction_entity_id, role,
                 strength, evidence_json, source
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (affiliation_id, owner_kind, owner_id, subject_entity_id, faction_entity_id, role,
             _clamp_float(strength, 0.0, 1.0, 1.0), dumps(evidence or {}), source),
        )
    append_journal(conn, owner_kind, owner_id, "world_affiliation_linked",
                   {"affiliation_id": affiliation_id, "subject_entity_id": subject_entity_id,
                    "faction_entity_id": faction_entity_id, "role": role}, source)
    return _decode_affiliation(conn.execute("SELECT * FROM world_affiliations WHERE id=?", (affiliation_id,)).fetchone())


def list_affiliations(conn, owner_kind: str, owner_id: str, *, entity_id: str | None = None,
                      limit: int = 50) -> list[dict[str, Any]]:
    """读取实体归属关系。

    输入可指定 subject 或 faction 的实体 id；输出是 active affiliations。函数只读
    数据库，供工具展示“谁属于哪个势力/组织/圈层”。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=? AND status='active'"
    if entity_id:
        where += " AND (subject_entity_id=? OR faction_entity_id=?)"
        params.extend([entity_id, entity_id])
    rows = conn.execute(
        f"SELECT * FROM world_affiliations {where} ORDER BY updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_affiliation(r) for r in rows]


def upsert_social_edge(conn, owner_kind: str, owner_id: str, *, source_entity_id: str,
                       target_entity_id: str, axis: str, value: float,
                       confidence: float = 0.6, visibility: str = "known",
                       evidence: dict[str, Any] | None = None,
                       source: str = "life_social") -> dict[str, Any]:
    """写入或更新一条有向社会关系边。

    输入是源实体、目标实体、世界观定义的关系轴和数值；输出是关系边。副作用是
    写 `social_edges` 和 journal。关系轴如亲密、敌意、上下级、盟友等均由世界观包
    定义，核心只维护数值、置信度、可见性和幂等更新。
    """
    if not _entity_belongs(conn, owner_kind, owner_id, source_entity_id):
        raise ValueError(f"source entity not found: {source_entity_id}")
    if not _entity_belongs(conn, owner_kind, owner_id, target_entity_id):
        raise ValueError(f"target entity not found: {target_entity_id}")
    axis = str(axis or "").strip()
    if not axis:
        raise ValueError("relationship axis is required")
    existing = conn.execute(
        """SELECT id FROM social_edges
           WHERE owner_kind=? AND owner_id=? AND source_entity_id=? AND target_entity_id=? AND axis=?""",
        (owner_kind, owner_id, source_entity_id, target_entity_id, axis),
    ).fetchone()
    if existing:
        edge_id = existing["id"]
        conn.execute(
            """UPDATE social_edges
               SET value=?, confidence=?, visibility=?, evidence_json=?, status='active',
                   source=?, updated_at=datetime('now')
               WHERE id=?""",
            (_clamp_float(value, -100.0, 100.0, 0.0), _clamp_float(confidence, 0.0, 1.0, 0.6),
             visibility, dumps(evidence or {}), source, edge_id),
        )
    else:
        edge_id = new_id("socedge")
        conn.execute(
            """INSERT INTO social_edges(
                 id, owner_kind, owner_id, source_entity_id, target_entity_id, axis,
                 value, confidence, visibility, evidence_json, source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (edge_id, owner_kind, owner_id, source_entity_id, target_entity_id, axis,
             _clamp_float(value, -100.0, 100.0, 0.0), _clamp_float(confidence, 0.0, 1.0, 0.6),
             visibility, dumps(evidence or {}), source),
        )
    append_journal(conn, owner_kind, owner_id, "social_edge_upserted",
                   {"edge_id": edge_id, "source_entity_id": source_entity_id,
                    "target_entity_id": target_entity_id, "axis": axis}, source)
    return _decode_edge(conn.execute("SELECT * FROM social_edges WHERE id=?", (edge_id,)).fetchone())


def list_social_edges(conn, owner_kind: str, owner_id: str, *, entity_id: str | None = None,
                      axis: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """读取社会关系边。

    输入可按实体或关系轴过滤；输出是 active edges。函数只读数据库，调用方包括
    `life_social edges` 和未来上下文/heartbeat 社会层。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=? AND status='active'"
    if entity_id:
        where += " AND (source_entity_id=? OR target_entity_id=?)"
        params.extend([entity_id, entity_id])
    if axis:
        where += " AND axis=?"
        params.append(axis)
    rows = conn.execute(
        f"SELECT * FROM social_edges {where} ORDER BY updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_edge(r) for r in rows]


def apply_reputation_event(conn, owner_kind: str, owner_id: str, *, subject_entity_id: str,
                           axis: str, delta: float, audience_entity_id: str | None = None,
                           reason: str | None = None, evidence_kind: str | None = None,
                           evidence_id: str | None = None,
                           evidence: dict[str, Any] | None = None,
                           source: str = "life_social") -> dict[str, Any]:
    """把一次社会后果写入声望账本。

    输入是主体实体、声望轴、增量和可选 audience；输出包含 reputation event 与聚合
    account。副作用是插入 `reputation_events`、更新/创建 `reputation_accounts`
    并写 journal。声望值限制在 -100..100；具体轴含义由世界观包定义。
    """
    if not _entity_belongs(conn, owner_kind, owner_id, subject_entity_id):
        raise ValueError(f"subject entity not found: {subject_entity_id}")
    audience = str(audience_entity_id or WORLD_AUDIENCE)
    if audience != WORLD_AUDIENCE and not _entity_belongs(conn, owner_kind, owner_id, audience):
        raise ValueError(f"audience entity not found: {audience}")
    axis = str(axis or "").strip()
    if not axis:
        raise ValueError("reputation axis is required")
    delta_value = _clamp_float(delta, -100.0, 100.0, 0.0)
    event_id = new_id("repevt")
    conn.execute(
        """INSERT INTO reputation_events(
             id, owner_kind, owner_id, subject_entity_id, audience_entity_id, axis,
             delta, reason, evidence_kind, evidence_id, evidence_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event_id, owner_kind, owner_id, subject_entity_id, audience, axis,
         delta_value, reason, evidence_kind, evidence_id, dumps(evidence or {}), source),
    )
    existing = conn.execute(
        """SELECT * FROM reputation_accounts
           WHERE owner_kind=? AND owner_id=? AND subject_entity_id=? AND audience_entity_id=? AND axis=?""",
        (owner_kind, owner_id, subject_entity_id, audience, axis),
    ).fetchone()
    if existing:
        account_id = existing["id"]
        next_value = _clamp_float(float(existing["value"]) + delta_value, -100.0, 100.0, 0.0)
        next_conf = _clamp_float(float(existing["confidence"]) + 0.05, 0.0, 1.0, 0.5)
        conn.execute(
            "UPDATE reputation_accounts SET value=?, confidence=?, updated_at=datetime('now') WHERE id=?",
            (next_value, next_conf, account_id),
        )
    else:
        account_id = new_id("repacct")
        conn.execute(
            """INSERT INTO reputation_accounts(
                 id, owner_kind, owner_id, subject_entity_id, audience_entity_id, axis,
                 value, confidence
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (account_id, owner_kind, owner_id, subject_entity_id, audience, axis,
             _clamp_float(delta_value, -100.0, 100.0, 0.0), 0.55),
        )
    append_journal(conn, owner_kind, owner_id, "reputation_event_recorded",
                   {"event_id": event_id, "account_id": account_id, "subject_entity_id": subject_entity_id,
                    "audience_entity_id": audience, "axis": axis, "delta": delta_value}, source)
    return {
        "event": _decode_reputation_event(conn.execute("SELECT * FROM reputation_events WHERE id=?", (event_id,)).fetchone()),
        "account": _row(conn.execute("SELECT * FROM reputation_accounts WHERE id=?", (account_id,)).fetchone()),
    }


def list_reputation_accounts(conn, owner_kind: str, owner_id: str, *,
                             subject_entity_id: str | None = None,
                             audience_entity_id: str | None = None,
                             axis: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """读取声望聚合账户。

    输入可按主体、audience 或轴过滤；输出为当前值列表。函数只读数据库，用于
    展示“谁在什么圈层里被怎么看”。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=? AND status='active'"
    if subject_entity_id:
        where += " AND subject_entity_id=?"
        params.append(subject_entity_id)
    if audience_entity_id:
        where += " AND audience_entity_id=?"
        params.append(str(audience_entity_id))
    if axis:
        where += " AND axis=?"
        params.append(axis)
    rows = conn.execute(
        f"SELECT * FROM reputation_accounts {where} ORDER BY ABS(value) DESC, updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_row(r) for r in rows]


def list_reputation_events(conn, owner_kind: str, owner_id: str, *,
                           subject_entity_id: str | None = None,
                           limit: int = 50) -> list[dict[str, Any]]:
    """读取声望事件流水。

    输入可指定主体实体；输出按创建时间倒序排列。函数只读数据库，方便追溯某个
    声望值为什么变化。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if subject_entity_id:
        where += " AND subject_entity_id=?"
        params.append(subject_entity_id)
    rows = conn.execute(
        f"SELECT * FROM reputation_events {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_reputation_event(r) for r in rows]


def record_evaluation(conn, owner_kind: str, owner_id: str, *, subject_entity_id: str,
                      axis: str, score: float, evaluator_entity_id: str | None = None,
                      target_kind: str = "entity", target_id: str | None = None,
                      reason: str | None = None, visibility: str = "known",
                      truth_layer: str = "social_perception",
                      evidence: dict[str, Any] | None = None,
                      source: str = "life_social") -> dict[str, Any]:
    """记录一次社会评价。

    输入表达“某评价者/群体如何评价某主体或事件”；输出 evaluation 行。评价不同于
    agent opinion：它是世界对主体的看法，可影响声望、邀请、排斥或流言。核心只
    存 axis/score/reason/visibility/truth_layer，具体规则由世界观包解释。
    """
    if not _entity_belongs(conn, owner_kind, owner_id, subject_entity_id):
        raise ValueError(f"subject entity not found: {subject_entity_id}")
    evaluator = str(evaluator_entity_id or WORLD_AUDIENCE)
    if evaluator != WORLD_AUDIENCE and not _entity_belongs(conn, owner_kind, owner_id, evaluator):
        raise ValueError(f"evaluator entity not found: {evaluator}")
    axis = str(axis or "").strip()
    if not axis:
        raise ValueError("evaluation axis is required")
    evaluation_id = new_id("eval")
    conn.execute(
        """INSERT INTO social_evaluations(
             id, owner_kind, owner_id, evaluator_entity_id, subject_entity_id,
             target_kind, target_id, axis, score, reason, visibility, truth_layer,
             evidence_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (evaluation_id, owner_kind, owner_id, evaluator, subject_entity_id,
         target_kind or "entity", target_id or subject_entity_id, axis,
         _clamp_float(score, -100.0, 100.0, 0.0), reason, visibility, truth_layer,
         dumps(evidence or {}), source),
    )
    append_journal(conn, owner_kind, owner_id, "social_evaluation_recorded",
                   {"evaluation_id": evaluation_id, "subject_entity_id": subject_entity_id,
                    "evaluator_entity_id": evaluator, "axis": axis}, source)
    return _decode_evaluation(conn.execute("SELECT * FROM social_evaluations WHERE id=?", (evaluation_id,)).fetchone())


def list_evaluations(conn, owner_kind: str, owner_id: str, *, subject_entity_id: str | None = None,
                     evaluator_entity_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """读取社会评价记录。

    输入可按被评价者或评价者过滤；输出保留 truth_layer 和 visibility，提醒调用方
    不要把社会观感当成客观事实。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=? AND status='active'"
    if subject_entity_id:
        where += " AND subject_entity_id=?"
        params.append(subject_entity_id)
    if evaluator_entity_id:
        where += " AND evaluator_entity_id=?"
        params.append(str(evaluator_entity_id))
    rows = conn.execute(
        f"SELECT * FROM social_evaluations {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_evaluation(r) for r in rows]


def record_rumor(conn, owner_kind: str, owner_id: str, *, content: str,
                 channel: str, subject_entity_id: str | None = None,
                 target_kind: str = "entity", target_id: str | None = None,
                 heat: float = 0.5, credibility: float = 0.3,
                 sentiment: str | None = None, visibility: str = "local",
                 truth_layer: str = "rumor_unverified",
                 evidence: dict[str, Any] | None = None,
                 source: str = "life_social") -> dict[str, Any]:
    """记录一条流言或未证实社会叙事。

    输入包含内容、渠道、热度、可信度和 truth_layer；输出 rumor 行。副作用是写
    `rumors` 和 journal。默认 truth_layer 为 `rumor_unverified`，调用方不得把它
    当事实写入事件或记忆，除非后续世界观规则确认。
    """
    content = str(content or "").strip()
    channel = str(channel or "").strip()
    if not content:
        raise ValueError("rumor content is required")
    if not channel:
        raise ValueError("rumor channel is required")
    if subject_entity_id and not _entity_belongs(conn, owner_kind, owner_id, subject_entity_id):
        raise ValueError(f"subject entity not found: {subject_entity_id}")
    rumor_id = new_id("rumor")
    conn.execute(
        """INSERT INTO rumors(
             id, owner_kind, owner_id, subject_entity_id, target_kind, target_id,
             content, channel, heat, credibility, sentiment, visibility, truth_layer,
             evidence_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rumor_id, owner_kind, owner_id, subject_entity_id, target_kind or "entity", target_id,
         content, channel, _clamp_float(heat, 0.0, 1.0, 0.5), _clamp_float(credibility, 0.0, 1.0, 0.3),
         sentiment, visibility, truth_layer, dumps(evidence or {}), source),
    )
    append_journal(conn, owner_kind, owner_id, "rumor_recorded",
                   {"rumor_id": rumor_id, "channel": channel, "truth_layer": truth_layer}, source)
    return _decode_rumor(conn.execute("SELECT * FROM rumors WHERE id=?", (rumor_id,)).fetchone())


def list_rumors(conn, owner_kind: str, owner_id: str, *, channel: str | None = None,
                subject_entity_id: str | None = None,
                status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
    """读取流言记录。

    输入可按渠道、主体和状态过滤；输出按热度和更新时间排序。函数只读数据库，
    保留 truth_layer 供上层区分未证实流言、社会观感和已确认事实。
    """
    params: list[Any] = [owner_kind, owner_id, status]
    where = "WHERE owner_kind=? AND owner_id=? AND status=?"
    if channel:
        where += " AND channel=?"
        params.append(channel)
    if subject_entity_id:
        where += " AND subject_entity_id=?"
        params.append(subject_entity_id)
    rows = conn.execute(
        f"SELECT * FROM rumors {where} ORDER BY heat DESC, updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_rumor(r) for r in rows]


def record_rumor_exposure(conn, owner_kind: str, owner_id: str, *, rumor_id: str,
                          entity_id: str, exposure_state: str = "heard",
                          reaction: str | None = None,
                          source: str = "life_social") -> dict[str, Any]:
    """记录某实体已经听到或传播过某条流言。

    输入是 rumor 与实体 id；输出 exposure 行。重复记录会更新状态和反应，保持幂等。
    副作用是写 `rumor_exposures` 和 journal，供后续传播/社交机会规则使用。
    """
    rumor = conn.execute(
        "SELECT id FROM rumors WHERE id=? AND owner_kind=? AND owner_id=?",
        (rumor_id, owner_kind, owner_id),
    ).fetchone()
    if not rumor:
        raise ValueError(f"rumor not found: {rumor_id}")
    if not _entity_belongs(conn, owner_kind, owner_id, entity_id):
        raise ValueError(f"entity not found: {entity_id}")
    existing = conn.execute(
        "SELECT id FROM rumor_exposures WHERE rumor_id=? AND entity_id=?",
        (rumor_id, entity_id),
    ).fetchone()
    if existing:
        exposure_id = existing["id"]
        conn.execute(
            """UPDATE rumor_exposures
               SET exposure_state=?, reaction=?, source=?, updated_at=datetime('now')
               WHERE id=?""",
            (exposure_state, reaction, source, exposure_id),
        )
    else:
        exposure_id = new_id("rumorexp")
        conn.execute(
            """INSERT INTO rumor_exposures(
                 id, owner_kind, owner_id, rumor_id, entity_id, exposure_state, reaction, source
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (exposure_id, owner_kind, owner_id, rumor_id, entity_id, exposure_state, reaction, source),
        )
    append_journal(conn, owner_kind, owner_id, "rumor_exposure_recorded",
                   {"rumor_id": rumor_id, "entity_id": entity_id, "exposure_state": exposure_state}, source)
    return _row(conn.execute("SELECT * FROM rumor_exposures WHERE id=?", (exposure_id,)).fetchone())


def list_rumor_exposures(conn, owner_kind: str, owner_id: str, *, rumor_id: str | None = None,
                         entity_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """读取流言触达记录。

    输入可按 rumor 或实体过滤；输出展示谁听过、谁传播过或谁压下了流言。函数只读
    数据库，是后续自动传播规则的证据来源。
    """
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if rumor_id:
        where += " AND rumor_id=?"
        params.append(rumor_id)
    if entity_id:
        where += " AND entity_id=?"
        params.append(entity_id)
    rows = conn.execute(
        f"SELECT * FROM rumor_exposures {where} ORDER BY updated_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_row(r) for r in rows]


def record_social_request(conn, owner_kind: str, owner_id: str, *, requester_entity_id: str | None,
                          target_entity_id: str | None, request_type: str,
                          topic: str = "unknown", summary: str | None = None,
                          details: dict[str, Any] | None = None,
                          quote: dict[str, Any] | None = None,
                          billing: dict[str, Any] | None = None,
                          privacy_level: str = "local",
                          linked_event_id: str | None = None,
                          linked_schedule_block_id: str | None = None,
                          linked_activity_id: str | None = None,
                          linked_occurrence_id: str | None = None,
                          linked_commission_id: str | None = None,
                          evidence: dict[str, Any] | None = None,
                          status: str = "open",
                          idempotency_key: str | None = None,
                          source: str = "life_social") -> dict[str, Any]:
    """Record a visitor/client request or wish in the social world.

    The table is intentionally generic: a worldview can later specialize wishes,
    blessings, purchase needs, and commission inquiries without losing the
    event-linked evidence captured here.
    """
    if requester_entity_id and not _entity_belongs(conn, owner_kind, owner_id, requester_entity_id):
        raise ValueError(f"requester entity not found: {requester_entity_id}")
    if target_entity_id and not _entity_belongs(conn, owner_kind, owner_id, target_entity_id):
        raise ValueError(f"target entity not found: {target_entity_id}")
    request_type = str(request_type or "").strip()
    if not request_type:
        raise ValueError("request_type is required")
    status_v = str(status or "open").strip() or "open"
    if status_v not in _REQUEST_STATUSES:
        raise ValueError(f"unknown social request status: {status_v}")
    topic = str(topic or "unknown").strip() or "unknown"
    idem = str(idempotency_key or f"{source}:{linked_event_id or 'none'}:{linked_occurrence_id or 'none'}:{request_type}:{topic}").strip()
    existing = conn.execute(
        "SELECT id FROM social_requests WHERE owner_kind=? AND owner_id=? AND idempotency_key=?",
        (owner_kind, owner_id, idem),
    ).fetchone()
    if existing:
        request_id = existing["id"]
        conn.execute(
            """UPDATE social_requests
               SET requester_entity_id=?, target_entity_id=?, request_type=?, topic=?, summary=?,
                   details_json=?, quote_json=?, billing_json=?, privacy_level=?,
                   linked_event_id=?, linked_schedule_block_id=?, linked_activity_id=?,
                   linked_occurrence_id=?, linked_commission_id=?, evidence_json=?, status=?,
                   source=?, updated_at=datetime('now'), accepted_at=CASE WHEN ?='accepted' AND accepted_at IS NULL THEN datetime('now') ELSE accepted_at END,
                   closed_at=CASE WHEN ? IN ('rejected','completed','expired','cancelled') THEN datetime('now') ELSE closed_at END
               WHERE id=?""",
            (requester_entity_id, target_entity_id, request_type, topic, summary,
             dumps(details or {}), dumps(quote or {}), dumps(billing or {}), privacy_level or "local",
             linked_event_id, linked_schedule_block_id, linked_activity_id, linked_occurrence_id,
             linked_commission_id, dumps(evidence or {}), status_v, source, status_v, status_v,
             request_id),
        )
    else:
        request_id = new_id("socreq")
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
        conn.execute(
            """INSERT INTO social_requests(
                 id, owner_kind, owner_id, requester_entity_id, target_entity_id,
                 request_type, topic, summary, details_json, quote_json, billing_json, privacy_level,
                 linked_event_id, linked_schedule_block_id, linked_activity_id,
                 linked_occurrence_id, linked_commission_id, evidence_json, status, source, idempotency_key,
                 accepted_at, closed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request_id, owner_kind, owner_id, requester_entity_id, target_entity_id,
             request_type, topic, summary, dumps(details or {}), dumps(quote or {}), dumps(billing or {}),
             privacy_level or "local", linked_event_id, linked_schedule_block_id, linked_activity_id,
             linked_occurrence_id, linked_commission_id, dumps(evidence or {}), status_v, source, idem,
             now if status_v == "accepted" else None,
             now if status_v in _REQUEST_TERMINAL_STATUSES else None),
        )
    append_journal(conn, owner_kind, owner_id, "social_request_recorded",
                   {"request_id": request_id, "request_type": request_type, "topic": topic,
                    "event_id": linked_event_id, "occurrence_id": linked_occurrence_id}, source)
    return _decode_request(conn.execute("SELECT * FROM social_requests WHERE id=?", (request_id,)).fetchone())


def list_social_requests(conn, owner_kind: str, owner_id: str, *,
                         requester_entity_id: str | None = None,
                         target_entity_id: str | None = None,
                         request_type: str | None = None,
                         status: str | None = None,
                         limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if requester_entity_id:
        where += " AND requester_entity_id=?"
        params.append(requester_entity_id)
    if target_entity_id:
        where += " AND target_entity_id=?"
        params.append(target_entity_id)
    if request_type:
        where += " AND request_type=?"
        params.append(request_type)
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(
        f"SELECT * FROM social_requests {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_request(r) for r in rows]


def transition_social_request(conn, owner_kind: str, owner_id: str, *, request_id: str,
                              action: str, actor_entity_id: str | None = None,
                              reason: str | None = None,
                              quote: dict[str, Any] | None = None,
                              billing: dict[str, Any] | None = None,
                              linked_event_id: str | None = None,
                              linked_commission_id: str | None = None,
                              evidence: dict[str, Any] | None = None,
                              source: str = "life_social") -> dict[str, Any]:
    """推进一个社会请求的生命周期。

    输入是 request id 和动作；输出包含更新后的 request 与 transition 账本记录。
    状态机覆盖接受、拒绝、完成、过期、取消、重开、转 event/commission、报价和账单
    绑定。它只维护通用生命周期，不创建具体世界观委托内容。
    """
    request_id = str(request_id or "").strip()
    action_l = str(action or "").strip().lower()
    if not request_id:
        raise ValueError("request_id is required")
    if not action_l:
        raise ValueError("request transition action is required")
    if actor_entity_id and not _entity_belongs(conn, owner_kind, owner_id, actor_entity_id):
        raise ValueError(f"actor entity not found: {actor_entity_id}")
    row = conn.execute(
        "SELECT * FROM social_requests WHERE id=? AND owner_kind=? AND owner_id=?",
        (request_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"social request not found: {request_id}")
    current = _decode_request(row)
    from_status = str(current.get("status") or "open")
    if from_status in _REQUEST_TERMINAL_STATUSES and action_l != "reopen":
        raise ValueError(f"social request is terminal: {from_status}")

    next_status = from_status
    if action_l in {"accept", "accepted"}:
        next_status = "accepted"
    elif action_l in {"reject", "rejected", "decline"}:
        next_status = "rejected"
    elif action_l in {"complete", "completed", "finish"}:
        next_status = "completed"
    elif action_l in {"expire", "expired"}:
        next_status = "expired"
    elif action_l in {"cancel", "cancelled"}:
        next_status = "cancelled"
    elif action_l in {"reopen", "open"}:
        next_status = "open"
    elif action_l in {"convert_event", "link_event", "event"}:
        if not linked_event_id:
            raise ValueError("linked_event_id is required for event conversion")
        next_status = "in_progress"
    elif action_l in {"convert_commission", "link_commission", "commission"}:
        if not linked_commission_id:
            raise ValueError("linked_commission_id is required for commission conversion")
        next_status = "in_progress"
    elif action_l in {"set_quote", "quote", "set_billing", "billing", "note"}:
        next_status = from_status
    else:
        raise ValueError(f"unknown social request transition action: {action_l}")
    if next_status not in _REQUEST_STATUSES:
        raise ValueError(f"unknown social request status: {next_status}")

    next_quote = dict(current.get("quote") or {})
    if quote:
        next_quote.update(quote)
    next_billing = dict(current.get("billing") or {})
    if billing:
        next_billing.update(billing)
    next_event_id = linked_event_id or current.get("linked_event_id")
    next_commission_id = linked_commission_id or current.get("linked_commission_id")
    closed = next_status in _REQUEST_TERMINAL_STATUSES
    accepted = next_status in {"accepted", "in_progress", "completed"}
    conn.execute(
        """UPDATE social_requests
           SET status=?, quote_json=?, billing_json=?, linked_event_id=?,
               linked_commission_id=?, updated_at=datetime('now'),
               accepted_at=CASE WHEN ?=1 AND accepted_at IS NULL THEN datetime('now') WHEN ?='open' THEN NULL ELSE accepted_at END,
               closed_at=CASE WHEN ?=1 THEN datetime('now') WHEN ?='open' THEN NULL ELSE closed_at END
           WHERE id=?""",
        (next_status, dumps(next_quote), dumps(next_billing), next_event_id, next_commission_id,
         1 if accepted else 0, next_status, 1 if closed else 0, next_status, request_id),
    )
    transition_id = new_id("socreqtx")
    conn.execute(
        """INSERT INTO social_request_transitions(
             id, owner_kind, owner_id, request_id, action, from_status, to_status,
             actor_entity_id, reason, quote_json, billing_json, linked_event_id,
             linked_commission_id, evidence_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (transition_id, owner_kind, owner_id, request_id, action_l, from_status, next_status,
         actor_entity_id, reason, dumps(quote or {}), dumps(billing or {}),
         linked_event_id, linked_commission_id, dumps(evidence or {}), source),
    )
    append_journal(conn, owner_kind, owner_id, "social_request_transitioned",
                   {"request_id": request_id, "action": action_l, "from_status": from_status, "to_status": next_status}, source)
    return {
        "request": _decode_request(conn.execute("SELECT * FROM social_requests WHERE id=?", (request_id,)).fetchone()),
        "transition": _decode_request_transition(conn.execute("SELECT * FROM social_request_transitions WHERE id=?", (transition_id,)).fetchone()),
    }


def list_social_request_transitions(conn, owner_kind: str, owner_id: str, *,
                                    request_id: str | None = None,
                                    limit: int = 50) -> list[dict[str, Any]]:
    """读取社会请求生命周期账本。"""
    params: list[Any] = [owner_kind, owner_id]
    where = "WHERE owner_kind=? AND owner_id=?"
    if request_id:
        where += " AND request_id=?"
        params.append(request_id)
    rows = conn.execute(
        f"SELECT * FROM social_request_transitions {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [int(limit)]),
    ).fetchall()
    return [_decode_request_transition(r) for r in rows]


def summary(conn, owner_kind: str, owner_id: str, *, canon: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成社会世界层的紧凑摘要。

    输入是 owner 和可选 Canon；输出包含槽定义、近期实体、声望账户、评价与流言。
    函数只读数据库，供 `life_social summary`、未来上下文注入和人工检查使用。
    """
    return {
        "slots": list_slot_definitions(conn, owner_kind, owner_id, canon=canon),
        "advisories": slot_advisories(conn, owner_kind, owner_id, canon=canon, limit=20),
        "entities": list_entities(conn, owner_kind, owner_id, limit=12),
        "reputation": list_reputation_accounts(conn, owner_kind, owner_id, limit=12),
        "evaluations": list_evaluations(conn, owner_kind, owner_id, limit=8),
        "rumors": list_rumors(conn, owner_kind, owner_id, limit=8),
        "requests": list_social_requests(conn, owner_kind, owner_id, limit=8),
    }


def _entity_belongs(conn, owner_kind: str, owner_id: str, entity_id: str | None) -> bool:
    if not entity_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM world_entities WHERE id=? AND owner_kind=? AND owner_id=? AND status='active'",
        (entity_id, owner_kind, owner_id),
    ).fetchone()
    return row is not None
