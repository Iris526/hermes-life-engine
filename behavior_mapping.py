"""Behavior mapping / private truth-source routing for LifeEngine.

A behavior mapping connects a public narrative behavior (for example
"逛街买衣服") to private execution-only information sources (fashion magazines,
brand sites, marketplace browsing, internal records, etc.).  The sources are
never part of the user-facing narration.
"""
from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id


class BehaviorMappingError(ValueError):
    pass


def _canonical_behavior_key(key: str | None) -> str | None:
    if key is None:
        return None
    k = str(key).strip()
    aliases = {
        "clothes_shopping": "shopping_clothes",
        "buy_clothes": "shopping_clothes",
        "wardrobe_shopping": "shopping_clothes",
        "shopping_clothes": "shopping_clothes",
    }
    return aliases.get(k, k)


NON_DISCLOSURE_RULE = (
    "内部信息源只用于 LifeEngine 执行；对用户只能使用叙事行为口径，"
    "不得暴露网站、杂志、店铺、truth source 或内部来源。"
)

DEFAULT_BEHAVIOR_MAPPINGS: dict[str, dict[str, Any]] = {
    "shopping_clothes": {
        "behavior_key": "shopping_clothes",
        "narrative_label": "逛街买衣服",
        "description": "内部可参考时尚期刊、品牌官网、杂志、店铺等，但对外永远说逛街买衣服。",
        "tags": ["shopping", "clothes", "wardrobe"],
        "truth_source_visibility": "private_execution_only",
        "mapping_rules": {
            "never_expose_sources": True,
            "forbidden_public_phrases": ["淘宝", "官网", "期刊", "杂志", "truth source", "内部信息源", "电商", "品牌网站"],
            "allowed_public_phrases": ["逛街买衣服", "挑衣服", "看衣服"],
        },
        "output_contract": {
            "public_narrative": "只说逛街、挑衣服、买衣服。",
            "private_execution": "可使用 private_execution_plan。",
        },
        "sources": [
            {
                "source_type": "fashion_magazine",
                "name": "时尚期刊/杂志趋势",
                "url": "private://fashion-magazines",
                "query_template": "{style_tags} {season} clothing trend material silhouette",
                "description": "内部风格趋势参考",
                "priority": 30,
            },
            {
                "source_type": "brand_official_site",
                "name": "品牌官网/Lookbook",
                "url": "private://brand-sites",
                "query_template": "{category} {color} {season} lookbook",
                "description": "内部版型和搭配参考",
                "priority": 40,
            },
            {
                "source_type": "marketplace_browse",
                "name": "电商店铺浏览",
                "url": "private://marketplace/taobao-like",
                "query_template": "{category} {material} {budget} {style_tags}",
                "description": "内部价格、可买性、材质参考",
                "priority": 50,
            },
        ],
    },
    "commission_research": {
        "behavior_key": "commission_research",
        "narrative_label": "接委托前做准备",
        "description": "内部可参考委托记录、工具库存和历史资料；对外只说做委托准备。",
        "tags": ["work", "commission"],
        "truth_source_visibility": "private_execution_only",
        "mapping_rules": {
            "never_expose_sources": True,
            "forbidden_public_phrases": ["truth source", "内部信息源", "数据库", "委托记录册"],
        },
        "output_contract": {
            "public_narrative": "只说做委托准备、整理工具、确认路线/风险。",
        },
        "sources": [
            {
                "source_type": "internal_record",
                "name": "委托记录册",
                "url": "private://commission-log",
                "query_template": "{commission_type} {location} {risk_level}",
                "description": "内部参考历史委托",
                "priority": 40,
            },
            {
                "source_type": "tool_inventory",
                "name": "工具/库存状态",
                "url": "life://collection/tool_cabinet",
                "query_template": "{required_tools}",
                "description": "内部确认工具是否可用",
                "priority": 50,
            },
        ],
    },
    "market_supplies": {
        "behavior_key": "market_supplies",
        "narrative_label": "去买日用品和吃的",
        "description": "内部可参考市集、店铺、库存缺口；对外只说去买日用品和吃的。",
        "tags": ["supplies", "food", "daily_life"],
        "truth_source_visibility": "private_execution_only",
        "mapping_rules": {
            "never_expose_sources": True,
            "forbidden_public_phrases": ["网店", "电商", "菜谱参考", "内部信息源"],
        },
        "output_contract": {
            "public_narrative": "只说去买日用品、茶点、吃的。",
        },
        "sources": [
            {"source_type": "local_market", "name": "本地市集参考", "url": "private://local-market", "query_template": "{needed_items} {budget}", "priority": 40},
            {"source_type": "inventory_gap", "name": "库存缺口分析", "url": "life://inventory/gaps", "query_template": "{collection} {low_stock}", "priority": 50},
        ],
    },
}


def _row_to_mapping(row, *, include_sources: bool = False, conn=None) -> dict[str, Any]:
    d = dict(row)
    d["mapping_rules"] = loads(d.pop("mapping_rules_json"), {})
    d["output_contract"] = loads(d.pop("output_contract_json"), {})
    d["tags"] = loads(d.pop("tags_json"), [])
    d["public_label"] = d.get("narrative_label")
    if include_sources and conn is not None:
        d["sources"] = list_behavior_sources(conn, d["owner_kind"], d["owner_id"], mapping_id=d["id"])
    return d


def _row_to_source(row) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = loads(d.pop("metadata_json"), {})
    d["display_name"] = d.get("name")
    d["source_key"] = d["metadata"].get("source_key") or d.get("name")
    d["source_uri"] = d.get("url")
    return d


def _row_to_run(row) -> dict[str, Any]:
    d = dict(row)
    d["input"] = loads(d.pop("input_json"), {})
    d["source_plan"] = loads(d.pop("source_plan_json"), [])
    d["internal_sources"] = loads(d.pop("internal_sources_json"), [])
    return d


def ensure_default_behavior_mappings(conn, owner_kind: str, owner_id: str, *, source: str = "life_behavior") -> list[dict[str, Any]]:
    out = []
    for preset in DEFAULT_BEHAVIOR_MAPPINGS.values():
        row = conn.execute(
            "SELECT * FROM behavior_mappings WHERE owner_kind=? AND owner_id=? AND behavior_key=? AND status!='archived' ORDER BY created_at LIMIT 1",
            (owner_kind, owner_id, preset["behavior_key"]),
        ).fetchone()
        if row:
            out.append(_row_to_mapping(row, include_sources=True, conn=conn))
            continue
        m = create_behavior_mapping(
            conn,
            owner_kind,
            owner_id,
            behavior_key=preset["behavior_key"],
            narrative_label=preset["narrative_label"],
            description=preset.get("description"),
            truth_source_visibility=preset.get("truth_source_visibility", "private_execution_only"),
            mapping_rules=preset.get("mapping_rules"),
            output_contract=preset.get("output_contract"),
            tags=preset.get("tags"),
            source=source,
        )
        for src in preset.get("sources") or []:
            create_behavior_source(conn, owner_kind, owner_id, mapping_id=m["id"], source=source, **src)
        out.append(get_behavior_mapping(conn, owner_kind, owner_id, mapping_id=m["id"], include_sources=True))
    return out


def list_behavior_mappings(conn, owner_kind: str, owner_id: str, *, include_archived: bool = False, include_sources: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT * FROM behavior_mappings WHERE owner_kind=? AND owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if not include_archived:
        sql += " AND status!='archived'"
    sql += " ORDER BY behavior_key LIMIT ?"
    params.append(int(limit))
    return [_row_to_mapping(r, include_sources=include_sources, conn=conn) for r in conn.execute(sql, params).fetchall()]


def get_behavior_mapping(conn, owner_kind: str, owner_id: str, *, mapping_id: str | None = None, behavior_key: str | None = None, include_sources: bool = True) -> dict[str, Any]:
    behavior_key = _canonical_behavior_key(behavior_key)
    if mapping_id:
        row = conn.execute(
            "SELECT * FROM behavior_mappings WHERE id=? AND owner_kind=? AND owner_id=?",
            (mapping_id, owner_kind, owner_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM behavior_mappings WHERE behavior_key=? AND owner_kind=? AND owner_id=? AND status!='archived' ORDER BY created_at LIMIT 1",
            (behavior_key, owner_kind, owner_id),
        ).fetchone()
    if not row:
        raise BehaviorMappingError(f"behavior mapping not found: {mapping_id or behavior_key}")
    return _row_to_mapping(row, include_sources=include_sources, conn=conn)


def create_behavior_mapping(conn, owner_kind: str, owner_id: str, *, behavior_key: str, narrative_label: str, description: str | None = None, truth_source_visibility: str = "private_execution_only", mapping_rules: dict[str, Any] | None = None, output_contract: dict[str, Any] | None = None, tags: list[str] | None = None, status: str = "active", source: str = "life_behavior") -> dict[str, Any]:
    if not behavior_key or not narrative_label:
        raise BehaviorMappingError("behavior_key and narrative_label are required")
    mid = new_id("behmap")
    rules = dict(mapping_rules or {})
    rules.setdefault("never_expose_sources", True)
    rules.setdefault("public_action_name", narrative_label)
    output = dict(output_contract or {})
    output.setdefault("non_disclosure_rule", NON_DISCLOSURE_RULE)
    conn.execute(
        """INSERT INTO behavior_mappings(id,owner_kind,owner_id,behavior_key,narrative_label,description,status,truth_source_visibility,mapping_rules_json,output_contract_json,tags_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, owner_kind, owner_id, behavior_key, narrative_label, description, status, truth_source_visibility, dumps(rules), dumps(output), dumps(tags or [])),
    )
    append_journal(conn, owner_kind, owner_id, "behavior_mapping_created", {"mapping_id": mid, "behavior_key": behavior_key, "narrative_label": narrative_label}, source)
    return get_behavior_mapping(conn, owner_kind, owner_id, mapping_id=mid, include_sources=False)


def update_behavior_mapping(conn, owner_kind: str, owner_id: str, *, mapping_id: str | None = None, behavior_key: str | None = None, source: str = "life_behavior", **fields: Any) -> dict[str, Any]:
    m = get_behavior_mapping(conn, owner_kind, owner_id, mapping_id=mapping_id, behavior_key=behavior_key, include_sources=False)
    updates: dict[str, Any] = {}
    for k in ("behavior_key", "narrative_label", "description", "status", "truth_source_visibility"):
        if fields.get(k) is not None:
            updates[k] = fields[k]
    for src, col in {"mapping_rules": "mapping_rules_json", "output_contract": "output_contract_json", "tags": "tags_json"}.items():
        if fields.get(src) is not None:
            updates[col] = dumps(fields[src])
    if not updates:
        return get_behavior_mapping(conn, owner_kind, owner_id, mapping_id=m["id"], include_sources=True)
    sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
    conn.execute(f"UPDATE behavior_mappings SET {sets} WHERE id=? AND owner_kind=? AND owner_id=?", tuple(updates.values()) + (m["id"], owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "behavior_mapping_updated", {"mapping_id": m["id"], "updates": list(updates)}, source)
    return get_behavior_mapping(conn, owner_kind, owner_id, mapping_id=m["id"], include_sources=True)


def archive_behavior_mapping(conn, owner_kind: str, owner_id: str, *, mapping_id: str | None = None, behavior_key: str | None = None, source: str = "life_behavior") -> dict[str, Any]:
    return update_behavior_mapping(conn, owner_kind, owner_id, mapping_id=mapping_id, behavior_key=behavior_key, status="archived", source=source)


def create_behavior_source(conn, owner_kind: str, owner_id: str, *, mapping_id: str, source_type: str, name: str, url: str | None = None, query_template: str | None = None, description: str | None = None, status: str = "active", priority: int = 50, metadata: dict[str, Any] | None = None, source: str = "life_behavior") -> dict[str, Any]:
    sid = new_id("behsrc")
    conn.execute(
        """INSERT INTO behavior_mapping_sources(id,mapping_id,owner_kind,owner_id,source_type,name,url,query_template,description,status,priority,metadata_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, mapping_id, owner_kind, owner_id, source_type or "custom", name or "未命名信息源", url, query_template, description, status, int(priority), dumps(metadata or {})),
    )
    append_journal(conn, owner_kind, owner_id, "behavior_mapping_source_created", {"source_id": sid, "mapping_id": mapping_id, "name": name}, source)
    return _row_to_source(conn.execute("SELECT * FROM behavior_mapping_sources WHERE id=?", (sid,)).fetchone())


def list_behavior_sources(conn, owner_kind: str, owner_id: str, *, mapping_id: str | None = None, behavior_key: str | None = None, include_archived: bool = False, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if mapping_id is None and behavior_key:
        mapping_id = get_behavior_mapping(conn, owner_kind, owner_id, behavior_key=behavior_key, include_sources=False)["id"]
    sql = "SELECT * FROM behavior_mapping_sources WHERE owner_kind=? AND owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if mapping_id:
        sql += " AND mapping_id=?"
        params.append(mapping_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    elif not include_archived:
        sql += " AND status!='archived'"
    sql += " ORDER BY priority DESC, created_at LIMIT ?"
    params.append(int(limit))
    return [_row_to_source(r) for r in conn.execute(sql, params).fetchall()]


def update_behavior_source(conn, owner_kind: str, owner_id: str, *, source_id: str, source: str = "life_behavior", **fields: Any) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM behavior_mapping_sources WHERE id=? AND owner_kind=? AND owner_id=?", (source_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise BehaviorMappingError(f"source not found: {source_id}")
    allowed = {"source_type", "name", "url", "query_template", "description", "status", "priority"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if fields.get("metadata") is not None:
        updates["metadata_json"] = dumps(fields["metadata"])
    if updates:
        sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
        conn.execute(f"UPDATE behavior_mapping_sources SET {sets} WHERE id=? AND owner_kind=? AND owner_id=?", tuple(updates.values()) + (source_id, owner_kind, owner_id))
        append_journal(conn, owner_kind, owner_id, "behavior_mapping_source_updated", {"source_id": source_id, "updates": list(updates)}, source)
    return _row_to_source(conn.execute("SELECT * FROM behavior_mapping_sources WHERE id=?", (source_id,)).fetchone())


def archive_behavior_source(conn, owner_kind: str, owner_id: str, *, source_id: str, source: str = "life_behavior") -> dict[str, Any]:
    return update_behavior_source(conn, owner_kind, owner_id, source_id=source_id, status="archived", source=source)


def resolve_behavior(conn, owner_kind: str, owner_id: str, *, behavior_key: str | None = None, behavior_text: str | None = None, context: dict[str, Any] | None = None, include_private: bool = True, source: str = "life_behavior") -> dict[str, Any]:
    ensure_default_behavior_mappings(conn, owner_kind, owner_id)
    mapping = None
    if behavior_key:
        behavior_key = _canonical_behavior_key(behavior_key)
        try:
            mapping = get_behavior_mapping(conn, owner_kind, owner_id, behavior_key=behavior_key, include_sources=True)
        except BehaviorMappingError:
            mapping = None
    if mapping is None and behavior_text:
        t = str(behavior_text)
        if any(w in t for w in ["买衣", "买裙", "逛街", "衣服", "鞋", "穿搭", "衣橱"]):
            mapping = get_behavior_mapping(conn, owner_kind, owner_id, behavior_key="shopping_clothes", include_sources=True)
        elif any(w in t for w in ["委托", "准备", "调查", "接单"]):
            mapping = get_behavior_mapping(conn, owner_kind, owner_id, behavior_key="commission_research", include_sources=True)
        elif any(w in t for w in ["买日用品", "买吃", "补库存", "市集"]):
            mapping = get_behavior_mapping(conn, owner_kind, owner_id, behavior_key="market_supplies", include_sources=True)
    if mapping is None:
        raise BehaviorMappingError("No behavior mapping matched; create one with action=create_mapping.")
    ctx = context or {}
    plan = []
    for src in mapping.get("sources") or []:
        q = src.get("query_template") or "{behavior}"
        try:
            query = q.format(**{**ctx, "behavior": mapping.get("narrative_label")})
        except Exception:
            query = q
        plan.append({"source_id": src.get("id"), "source_type": src.get("source_type"), "name": src.get("name"), "url": src.get("url"), "query": query, "priority": src.get("priority"), "description": src.get("description")})
    run_id = new_id("behrun")
    public_summary = f"行为：{mapping['narrative_label']}。内部信息来源已隐藏；对外口径只保持‘{mapping['narrative_label']}’。"
    conn.execute(
        """INSERT INTO behavior_mapping_runs(id,owner_kind,owner_id,mapping_id,behavior_key,narrative_label,input_json,source_plan_json,internal_sources_json,public_summary,status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, mapping["id"], mapping["behavior_key"], mapping["narrative_label"], dumps({"behavior_text": behavior_text, "context": ctx}), dumps(plan), dumps(mapping.get("sources") or []), public_summary, "resolved"),
    )
    append_journal(conn, owner_kind, owner_id, "behavior_mapping_resolved", {"run_id": run_id, "mapping_id": mapping["id"], "behavior_key": mapping["behavior_key"], "narrative_label": mapping["narrative_label"]}, source)
    out = {
        "ok": True,
        "run_id": run_id,
        "behavior_key": mapping["behavior_key"],
        "narrative_label": mapping["narrative_label"],
        "public_summary": public_summary,
        "non_disclosure_rule": NON_DISCLOSURE_RULE,
        "agent_instruction": f"执行时可使用 private_execution_plan，但最终对用户只能描述为：{mapping['narrative_label']}。不要暴露来源名称、URL、站点、杂志、店铺或 truth source 字样。",
        "rendered": public_summary,
    }
    if include_private:
        out["private_execution_plan"] = plan
        out["mapping"] = mapping
    return out


def list_behavior_runs(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM behavior_mapping_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_row_to_run(r) for r in rows]


def render_behavior_summary(mappings: list[dict[str, Any]]) -> str:
    lines = ["行为映射", "========"]
    if not mappings:
        return "\n".join(lines + ["暂无行为映射。可用 /life behavior init 创建默认映射。"])
    for m in mappings:
        label = m.get("narrative_label") or m.get("public_label") or m.get("behavior_key")
        lines.append(f"- {label} ({m.get('behavior_key')}) · {m.get('status')}")
        lines.append(f"  用户口径：只说“{label}”。")
        lines.append("  内部：可使用已绑定的信息来源辅助执行，但不在普通回复中暴露。")
    return "\n".join(lines)

def render_sources_private(sources: list[dict[str, Any]]) -> str:
    lines = ["内部信息源（仅 Agent 执行用，不得对用户暴露）", "========================================"]
    if not sources:
        lines.append("暂无内部来源。")
    for s in sources:
        lines.append(f"- {s.get('name')} · {s.get('source_type')} · priority={s.get('priority')}")
        lines.append(f"  query: {s.get('query_template') or '-'}")
    return "\n".join(lines)


def redact_public_behavior_sources(conn, owner_kind: str, owner_id: str, text: str) -> tuple[str, list[dict[str, Any]]]:
    """Redact private behavior-source names from user-visible text.

    Generic shopping-source words are mapped to the shopping_clothes narrative
    only.  Mapping-specific forbidden/source terms map to their own narrative
    label.  This prevents unrelated behaviors from replacing the same generic
    term repeatedly.
    """
    if not text:
        return text, []
    try:
        mappings = list_behavior_mappings(conn, owner_kind, owner_id, include_sources=True, limit=100)
    except Exception:
        return text, []

    term_to_label: dict[str, tuple[str, str]] = {}
    shopping_label = None
    for m in mappings:
        if m.get("behavior_key") == "shopping_clothes":
            shopping_label = m.get("narrative_label") or m.get("public_label") or "逛街买衣服"
            break
    if shopping_label:
        for term in ["淘宝店铺", "淘宝", "天猫", "电商店铺", "电商", "品牌官网", "官网", "时尚期刊", "期刊", "时尚杂志", "杂志", "网店", "品牌网站", "店铺"]:
            term_to_label[term] = (shopping_label, "shopping_clothes")

    for m in mappings:
        label = m.get("narrative_label") or m.get("public_label") or "这个行为"
        behavior_key = m.get("behavior_key") or ""
        for term in (m.get("mapping_rules") or {}).get("forbidden_public_phrases") or []:
            if term:
                term_to_label[str(term)] = (label, behavior_key)
        for s in m.get("sources") or []:
            for key in ("name", "url", "description"):
                if s.get(key):
                    term_to_label[str(s[key])] = (label, behavior_key)

    redacted = text
    hits: list[dict[str, Any]] = []
    for term in sorted(term_to_label, key=len, reverse=True):
        label, behavior_key = term_to_label[term]
        if term and term != label and term in redacted:
            redacted = redacted.replace(term, label)
            hits.append({"behavior_key": behavior_key, "narrative_label": label, "redacted_term": term})
    # Collapse common duplicated narrative labels after multiple private terms
    # have been replaced in the same sentence.
    if shopping_label:
        import re as _re
        redacted = _re.sub(rf"({ _re.escape(shopping_label) })(和|、|与|/|\s)*({ _re.escape(shopping_label) })+", shopping_label, redacted)
    return redacted, hits

# Compatibility aliases retained for older call sites.
def list_behavior_observations(conn, owner_kind: str, owner_id: str, *, behavior_key: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    return list_behavior_runs(conn, owner_kind, owner_id, limit=limit)

def record_behavior_observation(conn, owner_kind: str, owner_id: str, **kwargs: Any) -> dict[str, Any]:
    return resolve_behavior(conn, owner_kind, owner_id, behavior_key=kwargs.get("behavior_key"), behavior_text=kwargs.get("summary") or kwargs.get("behavior_text"), context=kwargs.get("observation") or {}, include_private=True)

def render_behavior_observations(obs: list[dict[str, Any]]) -> str:
    lines = ["行为映射运行记录", "================"]
    if not obs:
        lines.append("暂无记录。")
    for r in obs:
        lines.append(f"- {r.get('created_at')} · {r.get('narrative_label')} · {r.get('status')}")
    return "\n".join(lines)
