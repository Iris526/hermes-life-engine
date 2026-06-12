"""Outfit resolver, current-wearing snapshots, and collection action chains.

v0.12.8 makes cabinet/closet items operational: an agent can resolve a
natural outfit request into collection item refs, check asset completeness,
wear/return an outfit, and run a purchase -> intake -> asset-job -> candidate
chain without inventing items from thin air.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .time_utils import now_iso
from .collections import (
    DEFAULT_COLLECTION_PRESETS,
    ensure_default_collections,
    get_collection,
    get_collection_item,
    list_collection_items,
    list_item_assets,
    create_collection_item,
    set_item_asset_uri,
    check_out_item,
    return_item,
    update_collection_item,
    get_outfit_plan,
)
from .resources import apply_delta, ResourceError

CORE_COLLECTIONS = ["wardrobe", "shoe_cabinet", "sock_drawer", "accessory_cabinet", "vanity"]
_COLLECTION_LABELS = {
    "wardrobe": "衣橱",
    "shoe_cabinet": "鞋柜",
    "sock_drawer": "袜子抽屉",
    "accessory_cabinet": "配饰柜",
    "vanity": "梳妆台",
}

_COLOR_ALIASES = {
    "浅蓝": ["浅蓝", "淡蓝", "light blue", "sky blue", "blue"],
    "白": ["白", "白色", "white"],
    "黑": ["黑", "黑色", "black"],
    "红": ["红", "红色", "red"],
    "蓝": ["蓝", "蓝色", "blue"],
    "米": ["米", "米色", "beige"],
}


def _row_to_resolution(row) -> dict[str, Any]:
    d = dict(row)
    for k, default in {
        "query_json": {}, "resolved_refs_json": {}, "missing_json": [],
        "asset_completeness_json": {}, "context_json": {}
    }.items():
        if k in d:
            d[k.replace("_json", "")] = loads(d.pop(k), default)
    return d


def _row_to_snapshot(row) -> dict[str, Any]:
    d = dict(row)
    for k, default in {"refs_json": {}, "dirty_state_json": {}, "context_json": {}, "notes_json": {}}.items():
        if k in d:
            d[k.replace("_json", "")] = loads(d.pop(k), default)
    return d


def _row_to_asset_check(row) -> dict[str, Any]:
    d = dict(row)
    for k, default in {"missing_json": [], "needs_generation_json": [], "complete_json": []}.items():
        if k in d:
            d[k.replace("_json", "")] = loads(d.pop(k), default)
    return d


def _row_to_purchase(row) -> dict[str, Any]:
    d = dict(row)
    for k, default in {"need_json": {}, "purchase_json": {}, "resource_deltas_json": [], "result_json": {}}.items():
        if k in d:
            d[k.replace("_json", "")] = loads(d.pop(k), default)
    return d


def _norm_text(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _tokens_from_query(text: str) -> list[str]:
    text = str(text or "").strip()
    toks: list[str] = []
    for key, vals in _COLOR_ALIASES.items():
        if key in text or any(v.lower() in text.lower() for v in vals):
            toks.extend(vals)
    for chunk in text.replace("，", " ").replace(",", " ").replace("/", " ").split():
        if len(chunk) >= 2:
            toks.append(chunk)
    return list(dict.fromkeys([t.lower() for t in toks if t]))


def _score_item(item: dict[str, Any], tokens: list[str]) -> int:
    blob = _norm_text(item.get("name"), item.get("description"), item.get("item_type"), item.get("tags"), item.get("attributes"), item.get("material_spec"))
    score = 0
    for t in tokens:
        if t and t in blob:
            score += 8 if len(t) > 2 else 4
    if item.get("availability_state") == "available":
        score += 3
    if item.get("cleanliness_state") == "clean":
        score += 2
    if item.get("status") == "active":
        score += 1
    return score


def _best_item(conn, owner_kind: str, owner_id: str, collection_type: str, tokens: list[str], *, limit: int = 80) -> dict[str, Any] | None:
    items = list_collection_items(conn, owner_kind, owner_id, collection_type=collection_type, status="active", limit=limit)
    usable = [i for i in items if i.get("availability_state") == "available" and i.get("cleanliness_state") not in {"dirty", "laundry", "repair_needed"}]
    if not usable:
        return None
    ranked = sorted(usable, key=lambda i: _score_item(i, tokens), reverse=True)
    if tokens and _score_item(ranked[0], tokens) <= 0:
        # Fall back to first clean available item only for optional collections.
        return ranked[0]
    return ranked[0]


def asset_completeness_for_item(conn, owner_kind: str, owner_id: str, item_id: str) -> dict[str, Any]:
    item = get_collection_item(conn, owner_kind, owner_id, item_id, include_assets=True)
    reqs = (item.get("asset_bundle") or {}).get("requirements") or []
    assets = item.get("assets") or []
    available = {a.get("asset_type"): a for a in assets if a.get("status") == "available" and a.get("asset_uri")}
    pending = {a.get("asset_type"): a for a in assets if a.get("status") != "available" or not a.get("asset_uri")}
    missing_types = [r for r in reqs if r not in available]
    primary_asset = None
    for k in ["primary_image", "front_view", "main_display", "side_view"] + list(available.keys()):
        a = available.get(k)
        if a and a.get("asset_uri"):
            primary_asset = a.get("asset_uri")
            break
    return {
        "item_id": item_id,
        "item_name": item.get("name"),
        "complete": not missing_types,
        "asset_uri": primary_asset,
        "required": reqs,
        "available_assets": list(available.keys()),
        "missing_assets": missing_types,
        "pending_assets": list(pending.keys()),
    }


def asset_completeness_for_outfit(conn, owner_kind: str, owner_id: str, refs: dict[str, Any]) -> dict[str, Any]:
    needs: list[dict[str, Any]] = []
    complete: list[dict[str, Any]] = []
    for ctype, ref in refs.items():
        if not isinstance(ref, dict) or not ref.get("item_id"):
            continue
        chk = asset_completeness_for_item(conn, owner_kind, owner_id, ref["item_id"])
        ref["asset_uri"] = chk.get("asset_uri")
        ref["asset_complete"] = chk.get("complete")
        if chk.get("complete"):
            complete.append(chk)
        else:
            needs.append(chk)
    status = "complete" if not needs else "needs_generation"
    return {"status": status, "complete": complete, "needs_generation": needs, "missing_count": len(needs)}


def resolve_outfit(conn, owner_kind: str, owner_id: str, *, query_text: str | None = None, occasion: str = "daily", event_id: str | None = None, context: dict[str, Any] | None = None, source: str = "life_collection") -> dict[str, Any]:
    ensure_default_collections(conn, owner_kind, owner_id)
    query_text = query_text or "今日穿搭"
    tokens = _tokens_from_query(query_text)
    refs: dict[str, Any] = {}
    missing: list[dict[str, Any]] = []

    # Required-ish garment layer.
    for ctype in ["wardrobe", "shoe_cabinet", "accessory_cabinet", "vanity"]:
        item = _best_item(conn, owner_kind, owner_id, ctype, tokens)
        if item:
            refs[ctype] = {"collection_type": ctype, "item_id": item["id"], "name": item["name"], "state": "selected"}
        else:
            missing.append({"collection_type": ctype, "label": _COLLECTION_LABELS.get(ctype, ctype), "reason": "no clean available item"})
            refs[ctype] = {"collection_type": ctype, "missing": True, "state": "missing"}

    # Sock layer can explicitly be bare_legs.
    sock_intent_bare = any(t in str(query_text) for t in ["光腿", "bare legs", "不穿袜", "无袜"])
    sock = None if sock_intent_bare else _best_item(conn, owner_kind, owner_id, "sock_drawer", tokens)
    if sock:
        refs["sock_drawer"] = {"collection_type": "sock_drawer", "item_id": sock["id"], "name": sock["name"], "state": "selected"}
    else:
        refs["sock_drawer"] = {"collection_type": "sock_drawer", "state": "bare_legs" if sock_intent_bare or not list_collection_items(conn, owner_kind, owner_id, collection_type="sock_drawer", limit=1) else "missing", "missing": False if sock_intent_bare else bool(list_collection_items(conn, owner_kind, owner_id, collection_type="sock_drawer", limit=1))}

    completeness = asset_completeness_for_outfit(conn, owner_kind, owner_id, refs)
    item_ids = [r["item_id"] for r in refs.values() if isinstance(r, dict) and r.get("item_id")]
    outfit_plan_id = new_id("outfit")
    conn.execute(
        """INSERT INTO outfit_plans(id, owner_kind, owner_id, occasion, event_id, item_ids_json, context_json, reasoning_json, status)
             VALUES(?,?,?,?,?,?,?,?,?)""",
        (outfit_plan_id, owner_kind, owner_id, occasion, event_id, dumps(item_ids), dumps({"query_text": query_text, **(context or {})}), dumps({"resolver": "v0.12.8", "tokens": tokens, "missing": missing, "asset_completeness": completeness}), "resolved"),
    )
    resolution_id = new_id("outres")
    conn.execute(
        """INSERT INTO outfit_resolutions(id, owner_kind, owner_id, query_text, occasion, event_id, outfit_plan_id, status, query_json, resolved_refs_json, missing_json, asset_completeness_json, context_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (resolution_id, owner_kind, owner_id, query_text, occasion, event_id, outfit_plan_id, "resolved", dumps({"text": query_text, "tokens": tokens}), dumps(refs), dumps(missing), dumps(completeness), dumps(context or {})),
    )
    append_journal(conn, owner_kind, owner_id, "outfit_resolved", {"resolution_id": resolution_id, "outfit_plan_id": outfit_plan_id, "query_text": query_text, "missing": missing, "asset_status": completeness.get("status")}, source)
    out = get_outfit_resolution(conn, owner_kind, owner_id, resolution_id)
    plan = get_outfit_plan(conn, owner_kind, owner_id, outfit_plan_id)
    return {"ok": True, "resolution": out, "outfit_plan": plan, "outfit_plan_id": outfit_plan_id, "rendered": render_outfit_resolution(out)}


def get_outfit_resolution(conn, owner_kind: str, owner_id: str, resolution_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM outfit_resolutions WHERE id=? AND owner_kind=? AND owner_id=?", (resolution_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"outfit resolution not found: {resolution_id}")
    return _row_to_resolution(row)


def list_outfit_resolutions(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM outfit_resolutions WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_row_to_resolution(r) for r in rows]


def create_outfit_snapshot(conn, owner_kind: str, owner_id: str, *, outfit_plan_id: str | None = None, resolution_id: str | None = None, event_id: str | None = None, source: str = "life_collection") -> dict[str, Any]:
    if resolution_id and not outfit_plan_id:
        res = get_outfit_resolution(conn, owner_kind, owner_id, resolution_id)
        outfit_plan_id = res.get("outfit_plan_id")
        refs = res.get("resolved_refs") or {}
    else:
        if not outfit_plan_id:
            raise ValueError("outfit_plan_id or resolution_id is required")
        plan = get_outfit_plan(conn, owner_kind, owner_id, outfit_plan_id)
        refs = {}
        for item_id in plan.get("item_ids") or []:
            item = get_collection_item(conn, owner_kind, owner_id, item_id)
            col = conn.execute("SELECT collection_type FROM item_collections WHERE id=?", (item["collection_id"],)).fetchone()
            ctype = col["collection_type"] if col else "custom"
            refs[ctype] = {"collection_type": ctype, "item_id": item_id, "name": item.get("name"), "state": "selected"}
    completeness = asset_completeness_for_outfit(conn, owner_kind, owner_id, refs)
    snapshot_id = new_id("wear")
    conn.execute(
        """INSERT INTO outfit_snapshots(id, owner_kind, owner_id, outfit_plan_id, event_id, status, worn_at, refs_json, dirty_state_json, context_json, notes_json, asset_completeness_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, owner_kind, owner_id, outfit_plan_id, event_id, "wearing", now_iso(), dumps(refs), dumps({}), dumps({"source": source}), dumps({}), dumps(completeness)),
    )
    for ref in refs.values():
        if isinstance(ref, dict) and ref.get("item_id"):
            try:
                check_out_item(conn, owner_kind, owner_id, item_id=ref["item_id"], reason="wear_outfit", event_id=event_id, source=source)
            except Exception:
                pass
    conn.execute("UPDATE outfit_plans SET status='wearing', updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?", (outfit_plan_id, owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "outfit_snapshot_worn", {"snapshot_id": snapshot_id, "outfit_plan_id": outfit_plan_id, "item_ids": [r.get("item_id") for r in refs.values() if isinstance(r, dict)]}, source)
    return {"ok": True, "snapshot": get_current_outfit(conn, owner_kind, owner_id), "rendered": render_current_outfit(get_current_outfit(conn, owner_kind, owner_id))}


def get_current_outfit(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM outfit_snapshots WHERE owner_kind=? AND owner_id=? AND status='wearing' ORDER BY worn_at DESC LIMIT 1", (owner_kind, owner_id)).fetchone()
    return _row_to_snapshot(row) if row else None


def list_outfit_snapshots(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM outfit_snapshots WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def return_current_outfit(conn, owner_kind: str, owner_id: str, *, snapshot_id: str | None = None, cleanliness_state: str = "dirty", source: str = "life_collection") -> dict[str, Any]:
    snap = None
    if snapshot_id:
        row = conn.execute("SELECT * FROM outfit_snapshots WHERE id=? AND owner_kind=? AND owner_id=?", (snapshot_id, owner_kind, owner_id)).fetchone()
        snap = _row_to_snapshot(row) if row else None
    else:
        snap = get_current_outfit(conn, owner_kind, owner_id)
    if not snap:
        return {"ok": True, "status": "no_current_outfit", "rendered": "当前没有记录中的穿着。"}
    refs = snap.get("refs") or {}
    dirty_state: dict[str, Any] = {}
    for ref in refs.values():
        if isinstance(ref, dict) and ref.get("item_id"):
            try:
                return_item(conn, owner_kind, owner_id, item_id=ref["item_id"], cleanliness_state=cleanliness_state, reason="return_outfit", event_id=snap.get("event_id"), source=source)
                dirty_state[ref["item_id"]] = cleanliness_state
            except Exception as exc:
                dirty_state[ref.get("item_id")] = f"return_failed:{exc}"
    conn.execute("UPDATE outfit_snapshots SET status='returned', removed_at=?, dirty_state_json=?, updated_at=datetime('now') WHERE id=?", (now_iso(), dumps(dirty_state), snap["id"]))
    if snap.get("outfit_plan_id"):
        conn.execute("UPDATE outfit_plans SET status='returned', updated_at=datetime('now') WHERE id=?", (snap["outfit_plan_id"],))
    append_journal(conn, owner_kind, owner_id, "outfit_snapshot_returned", {"snapshot_id": snap["id"], "dirty_state": dirty_state}, source)
    return {"ok": True, "snapshot_id": snap["id"], "dirty_state": dirty_state, "rendered": "已回库当前穿搭，相关条目已标记为 " + cleanliness_state + "。"}


def check_outfit_assets(conn, owner_kind: str, owner_id: str, *, item_id: str | None = None, outfit_plan_id: str | None = None, resolution_id: str | None = None) -> dict[str, Any]:
    target_kind = "unknown"; target_id = item_id or outfit_plan_id or resolution_id
    refs: dict[str, Any] = {}
    if item_id:
        target_kind = "item"
        chk = asset_completeness_for_item(conn, owner_kind, owner_id, item_id)
        missing = [chk] if not chk.get("complete") else []
        complete = [chk] if chk.get("complete") else []
    else:
        if resolution_id:
            target_kind = "resolution"
            res = get_outfit_resolution(conn, owner_kind, owner_id, resolution_id)
            refs = res.get("resolved_refs") or {}
        elif outfit_plan_id:
            target_kind = "outfit_plan"
            plan = get_outfit_plan(conn, owner_kind, owner_id, outfit_plan_id)
            for iid in plan.get("item_ids") or []:
                item = get_collection_item(conn, owner_kind, owner_id, iid)
                col = conn.execute("SELECT collection_type FROM item_collections WHERE id=?", (item["collection_id"],)).fetchone()
                refs[col["collection_type"] if col else iid] = {"item_id": iid}
        comp = asset_completeness_for_outfit(conn, owner_kind, owner_id, refs)
        missing = comp.get("needs_generation") or []
        complete = comp.get("complete") or []
    status = "complete" if not missing else "needs_generation"
    check_id = new_id("assetchk")
    conn.execute("""INSERT INTO collection_asset_checks(id, owner_kind, owner_id, target_kind, target_id, status, missing_json, needs_generation_json, complete_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (check_id, owner_kind, owner_id, target_kind, target_id, status, dumps(missing), dumps(missing), dumps(complete)))
    return {"ok": True, "check": _row_to_asset_check(conn.execute("SELECT * FROM collection_asset_checks WHERE id=?", (check_id,)).fetchone()), "rendered": render_asset_check(status, missing, complete)}


def purchase_to_collection(conn, owner_kind: str, owner_id: str, *, collection_type: str = "wardrobe", name: str, description: str | None = None, price: float | None = None, money_key: str | None = None, need: dict[str, Any] | None = None, behavior_key: str | None = "shopping_clothes", source: str = "life_collection") -> dict[str, Any]:
    ensure_default_collections(conn, owner_kind, owner_id)
    deltas = []
    if price is not None and money_key:
        try:
            deltas.append(apply_delta(conn, owner_kind, owner_id, money_key, -abs(float(price)), operation="consume", reason=f"purchase {name}", source="collection_purchase"))
        except ResourceError as exc:
            return {"ok": False, "error": str(exc), "rendered": f"购买失败：{exc}"}
    item = create_collection_item(conn, owner_kind, owner_id, collection_type=collection_type, name=name, description=description, source=source)
    chain_id = new_id("purchchain")
    conn.execute("""INSERT INTO collection_purchase_chains(id, owner_kind, owner_id, behavior_key, collection_item_id, need_json, purchase_json, resource_deltas_json, status, result_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (chain_id, owner_kind, owner_id, behavior_key, item["id"], dumps(need or {}), dumps({"name": name, "description": description, "price": price, "money_key": money_key, "collection_type": collection_type}), dumps(deltas), "item_intaked_assets_pending", dumps({"item_id": item["id"], "asset_status": "needs_generation"})))
    append_journal(conn, owner_kind, owner_id, "collection_purchase_chain", {"chain_id": chain_id, "item_id": item["id"], "collection_type": collection_type, "price": price, "money_key": money_key}, source)
    return {"ok": True, "purchase_chain": _row_to_purchase(conn.execute("SELECT * FROM collection_purchase_chains WHERE id=?", (chain_id,)).fetchone()), "item": item, "rendered": f"已完成购买→入柜链路：{name} 已入 {_COLLECTION_LABELS.get(collection_type, collection_type)}，资产图待生成。"}


def list_purchase_chains(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM collection_purchase_chains WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_row_to_purchase(r) for r in rows]


def render_outfit_resolution(res: dict[str, Any]) -> str:
    lines = ["穿搭解析", "========", f"请求：{res.get('query_text')}", f"场合：{res.get('occasion')}"]
    refs = res.get("resolved_refs") or {}
    for ctype in CORE_COLLECTIONS:
        ref = refs.get(ctype) or {}
        label = _COLLECTION_LABELS.get(ctype, ctype)
        if ref.get("item_id"):
            suffix = ""
            if ref.get("asset_complete") is False:
                suffix = "（资产图待补全）"
            lines.append(f"- {label}：{ref.get('name')} · {ref.get('item_id')}{suffix}")
        elif ref.get("state") == "bare_legs":
            lines.append(f"- {label}：光腿 / 不使用袜子层")
        else:
            lines.append(f"- {label}：缺失，需要入库或维护")
    comp = res.get("asset_completeness") or {}
    lines.append(f"资产完整度：{comp.get('status', 'unknown')}；待生成 {len(comp.get('needs_generation') or [])} 项。")
    return "\n".join(lines)


def render_current_outfit(snap: dict[str, Any] | None) -> str:
    if not snap:
        return "当前穿着\n========\n暂无当前穿着记录。"
    lines = ["当前穿着", "========", f"状态：{snap.get('status')}；穿上时间：{snap.get('worn_at')}"]
    refs = snap.get("refs") or {}
    for ctype in CORE_COLLECTIONS:
        ref = refs.get(ctype) or {}
        label = _COLLECTION_LABELS.get(ctype, ctype)
        if ref.get("item_id"):
            lines.append(f"- {label}：{ref.get('name')} · {ref.get('item_id')}")
        elif ref.get("state") == "bare_legs":
            lines.append(f"- {label}：光腿")
        else:
            lines.append(f"- {label}：未使用 / 缺失")
    return "\n".join(lines)


def render_asset_check(status: str, missing: list[dict[str, Any]], complete: list[dict[str, Any]]) -> str:
    lines = ["资产完整度检查", "==============", f"状态：{status}"]
    if missing:
        lines.append("待生成：")
        for m in missing:
            lines.append(f"- {m.get('item_name')}：缺 {', '.join(m.get('missing_assets') or [])}")
    if complete:
        lines.append("已完整：" + "、".join([str(c.get('item_name')) for c in complete]))
    return "\n".join(lines)
