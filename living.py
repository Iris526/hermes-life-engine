"""LifeEngine living layer: Canon consistency, day rhythm, concrete-life presets.

v0.12.5 turns the mature LifeEngine runtime from a state/event manager toward a
self-life simulator.  This module deliberately stays above raw SQL and below the
Hermes UI: it produces human-readable reports and LifeOps plans that the runtime
can commit through the normal validator / journal / receipt path.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from .canon import get_active_canon
from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id
from .time_utils import now_iso


def _norm_tz(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    text = v.strip()
    aliases = {"JST": "Asia/Tokyo", "CST_CN": "Asia/Shanghai", "UTC+9": "Asia/Tokyo"}
    return aliases.get(text, text)


def _flatten(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            out.append((path, v))
            out.extend(_flatten(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    return out


def canon_consistency_check(conn, owner_kind: str, owner_id: str, *, persist: bool = True) -> dict[str, Any]:
    """Check internal Canon consistency beyond mere required-setting presence."""
    canon = get_active_canon(conn, owner_kind, owner_id) or {}
    issues: list[dict[str, Any]] = []

    def issue(kind: str, severity: str, title: str, message: str, suggestion: str | None = None, paths: list[str] | None = None) -> None:
        issues.append({"kind": kind, "severity": severity, "title": title, "message": message, "suggestion": suggestion or "", "paths": paths or []})

    bindings = ((canon.get("truth_sources") or {}).get("bindings") or {}) if isinstance(canon, dict) else {}
    time_binding = bindings.get("time") or bindings.get("clock") or {}
    schedule_rules = canon.get("schedule_rules") or {}
    tz_values = []
    for path, val in [
        ("truth_sources.bindings.time.value", time_binding.get("value") if isinstance(time_binding, dict) else None),
        ("truth_sources.bindings.time.timezone", time_binding.get("timezone") if isinstance(time_binding, dict) else None),
        ("schedule_rules.timezone", schedule_rules.get("timezone") if isinstance(schedule_rules, dict) else None),
    ]:
        tz = _norm_tz(val)
        if tz and ("/" in tz or tz.upper() == "UTC"):
            tz_values.append((path, tz))
    uniq_tz = sorted({tz for _, tz in tz_values})
    if len(uniq_tz) > 1:
        issue(
            "time_timezone_mismatch", "warning", "时间 / 时区设定不一致",
            "Canon 里同时出现了不同的时区：" + "；".join([f"{p}={v}" for p, v in tz_values]),
            "统一 truth_sources.time.timezone 与 schedule_rules.timezone。", [p for p, _ in tz_values]
        )

    # Currency vs money.* resources.
    currency_binding = bindings.get("currency") or bindings.get("money") or {}
    currency = None
    if isinstance(currency_binding, dict):
        currency = currency_binding.get("currency") or currency_binding.get("value") or currency_binding.get("code")
    resources = ((canon.get("resources") or {}).get("definitions") or {}) if isinstance(canon, dict) else {}
    money_keys = [k for k in resources.keys() if str(k).startswith("money.")]
    if currency and money_keys:
        expected = f"money.{str(currency).lower()}"
        if expected not in {k.lower() for k in money_keys}:
            issue("currency_resource_mismatch", "warning", "货币设定和 money 资源不一致", f"currency={currency}，但资源里是 {', '.join(money_keys)}。", f"补充或改名为 {expected}，或调整 currency 绑定。", ["truth_sources.bindings.currency", "resources.definitions"])

    weather = bindings.get("weather") or {}
    if isinstance(weather, dict):
        authority = weather.get("authority")
        if authority in {"user_current_location", "external_tool"} and not (weather.get("location") or weather.get("parameters") or weather.get("source_location") or weather.get("fallback")):
            issue("weather_location_ambiguous", "info", "天气真相源缺少地点/回退说明", "天气绑定到了真实来源，但没有明确 location / user binding / fallback。", "例如：location=user_current_location，fallback=unknown。", ["truth_sources.bindings.weather"])
        if authority in {"narrative_simulator", "random_weather"} and not (weather.get("rules") or weather.get("mode")):
            issue("weather_virtual_rule_missing", "info", "虚拟天气缺少规则", "天气使用叙事/随机模拟，但没有 mode 或 rules。", "例如：mode=random_local 或 rules=符合第七城季节。", ["truth_sources.bindings.weather"])

    # Look for old delete markers or likely stale keys.
    stale = []
    for path, value in _flatten(canon):
        low_path = path.lower()
        low_val = str(value).lower() if isinstance(value, str) else ""
        if "__deleted__" in low_path or "deleted" in low_path or low_val in {"__delete__", "deleted", "remove_me"}:
            stale.append(path)
    if stale:
        issue("stale_delete_marker", "warning", "Canon 里残留旧删除/废弃标记", "发现疑似旧删除标记：" + "、".join(stale[:8]), "清理 CanonDraft 后重新 commit。", stale[:8])

    status = "ok"
    if any(i["severity"] == "error" for i in issues):
        status = "error"
    elif any(i["severity"] == "warning" for i in issues):
        status = "warning"
    elif issues:
        status = "info"
    rendered = render_canon_consistency(status, issues)
    report = {"ok": status in {"ok", "info"}, "status": status, "issues": issues, "conflict_count": sum(1 for i in issues if i["severity"] == "error"), "warning_count": sum(1 for i in issues if i["severity"] == "warning"), "rendered": rendered}
    if persist:
        rid = new_id("canoncheck")
        conn.execute(
            """INSERT INTO canon_consistency_reports(id, owner_kind, owner_id, status, conflict_count, warning_count, issues_json, rendered_text)
                 VALUES(?,?,?,?,?,?,?,?)""",
            (rid, owner_kind, owner_id, status, report["conflict_count"], report["warning_count"], dumps(issues), rendered),
        )
        append_audit(conn, owner_kind, owner_id, "canon_consistency_check", "warning" if status == "warning" else "info", f"Canon consistency status={status}", {"report_id": rid, "issues": len(issues)})
        report["id"] = rid
    return report


def render_canon_consistency(status: str, issues: list[dict[str, Any]]) -> str:
    lines = ["LifeEngine Canon 一致性检查", "=========================="]
    if not issues:
        lines.append("状态：没有发现明显不一致。")
    else:
        lines.append(f"状态：{status}；发现 {len(issues)} 项。")
        for idx, it in enumerate(issues, 1):
            lines.append(f"{idx}. [{it.get('severity')}] {it.get('title')} — {it.get('message')}")
            if it.get("suggestion"):
                lines.append(f"   建议：{it.get('suggestion')}")
    return "\n".join(lines)


GUIMINGGUAN_INVENTORY = [
    {"name": "符纸", "category": "daily_supply", "subcategory": "talisman", "quantity": 24, "unit": "张", "location": "归明观·偏柜", "notes": "日常净符和小委托会消耗。"},
    {"name": "朱砂墨", "category": "daily_supply", "subcategory": "ink", "quantity": 1, "unit": "瓶", "location": "归明观·案头"},
    {"name": "香", "category": "daily_supply", "subcategory": "incense", "quantity": 18, "unit": "支", "location": "归明观·香盒"},
    {"name": "小型结界仪", "category": "tool", "subcategory": "barrier_meter", "quantity": 1, "unit": "台", "condition": "good", "location": "随身工具包"},
    {"name": "铜铃", "category": "tool", "subcategory": "ritual_bell", "quantity": 1, "unit": "只", "location": "随身"},
    {"name": "归明观钥匙", "category": "tool", "subcategory": "key", "quantity": 1, "unit": "把", "location": "随身"},
    {"name": "委托记录册", "category": "book", "subcategory": "commission_log", "quantity": 1, "unit": "本", "location": "归明观·柜台"},
    {"name": "干净道袍", "category": "clothing", "subcategory": "robe", "quantity": 2, "unit": "套", "condition": "clean", "location": "归明观·衣柜"},
    {"name": "茶叶", "category": "food", "subcategory": "tea", "quantity": 1, "unit": "罐", "location": "归明观·茶柜"},
    {"name": "十二城小点心", "category": "food", "subcategory": "snack", "quantity": 3, "unit": "份", "location": "归明观·小柜"},
]

GUIMINGGUAN_RESOURCES = [
    {"key": "money.lingzhu", "display_name": "灵铢", "resource_class": "fungible", "unit": "枚", "min_value": 0, "max_value": None, "initial": 120},
    {"key": "daily_cost.lingzhu", "display_name": "每日基础开销", "resource_class": "fungible", "unit": "枚/日", "min_value": 0, "max_value": None, "initial": 8},
    {"key": "commission_income.lingzhu", "display_name": "委托收入累计", "resource_class": "fungible", "unit": "枚", "min_value": 0, "max_value": None, "initial": 0},
    {"key": "supplies.talisman_paper", "display_name": "符纸库存", "resource_class": "consumable", "unit": "张", "min_value": 0, "max_value": None, "initial": 24},
    {"key": "supplies.incense", "display_name": "香库存", "resource_class": "consumable", "unit": "支", "min_value": 0, "max_value": None, "initial": 18},
    {"key": "tools.barrier_meter_condition", "display_name": "结界仪状态", "resource_class": "state", "unit": "points", "min_value": 0, "max_value": 100, "initial": 86},
    {"key": "wardrobe.clean_outfits", "display_name": "干净衣物", "resource_class": "consumable", "unit": "套", "min_value": 0, "max_value": None, "initial": 2},
]


def inventory_preset_ops(preset: str = "guimingguan") -> list[dict[str, Any]]:
    if preset not in {"guimingguan", "mingdeng", "taoist_temple", "default"}:
        preset = "guimingguan"
    ops: list[dict[str, Any]] = []
    for res in GUIMINGGUAN_RESOURCES:
        ops.append({"type": "RESOURCE_DEFINE", "payload": dict(res)})
    for item in GUIMINGGUAN_INVENTORY:
        ops.append({"type": "CREATE_INVENTORY_ITEM", "payload": {**item, "attributes": {"preset": preset}, "source": "living_inventory_preset"}})
    return ops


def _time_for(date_key: str, hhmm: str, tz: str) -> str:
    return f"{date_key}T{hhmm}:00+09:00" if tz == "Asia/Tokyo" else f"{date_key}T{hhmm}:00"


def rhythm_templates(date_key: str | None = None, tz: str = "Asia/Tokyo", preset: str = "guimingguan") -> list[dict[str, Any]]:
    date_key = date_key or datetime.now(ZoneInfo(tz)).date().isoformat()
    return [
        {"title": "归明观晨巡与开观", "start": _time_for(date_key, "07:30", tz), "end": _time_for(date_key, "08:05", tz), "event_type": "routine", "event_category": "maintenance", "activity_domain": "temple_morning", "resource_costs": {"energy": -4, "mood": 2}, "tags": ["晨巡", "开观", "归明观"], "worth_diary": False},
        {"title": "打扫香案并补符纸", "start": _time_for(date_key, "08:20", tz), "end": _time_for(date_key, "08:55", tz), "event_type": "temple_chores", "event_category": "maintenance", "activity_domain": "altar_upkeep", "resource_costs": {"energy": -5, "supplies.incense": -1}, "tags": ["香案", "符纸", "日常"], "worth_diary": False},
        {"title": "检查小型结界工具包", "start": _time_for(date_key, "09:40", tz), "end": _time_for(date_key, "10:15", tz), "event_type": "inspection", "event_category": "work", "activity_domain": "barrier_tools", "resource_costs": {"focus": -5, "tools.barrier_meter_condition": -1}, "tags": ["结界仪", "工具包"], "worth_diary": False},
        {"title": "接一个低风险净符委托", "start": _time_for(date_key, "13:30", tz), "end": _time_for(date_key, "15:00", tz), "event_type": "commission", "event_category": "work", "activity_domain": "low_risk_talisman_commission", "resource_costs": {"energy": -12, "focus": -10, "supplies.talisman_paper": -3}, "tags": ["小委托", "净符", "十二城"], "worth_diary": True, "worth_proactive": True},
        {"title": "傍晚记账与灵铢收支整理", "start": _time_for(date_key, "17:40", tz), "end": _time_for(date_key, "18:10", tz), "event_type": "bookkeeping", "event_category": "finance", "activity_domain": "temple_accounts", "resource_costs": {"focus": -4}, "tags": ["记账", "灵铢"], "worth_diary": True},
        {"title": "写一张给 Ringo 的小纸条草稿", "start": _time_for(date_key, "21:30", tz), "end": _time_for(date_key, "21:45", tz), "event_type": "proactive_note", "event_category": "relationship", "activity_domain": "pending_share", "resource_costs": {"mood": 1, "focus": -2}, "tags": ["Ringo", "小纸条", "pending"], "worth_proactive": True},
    ]


def abstract_goal_children(date_key: str | None = None, tz: str = "Asia/Tokyo", preset: str = "guimingguan") -> list[dict[str, Any]]:
    out = []
    for item in rhythm_templates(date_key, tz, preset)[:4]:
        out.append({
            "title": item["title"],
            "event_type": item["event_type"],
            "event_category": item["event_category"],
            "activity_domain": item["activity_domain"],
            "source": "life_rhythm_decomposer",
            "status": "planned",
            "importance": 55 if item.get("event_category") != "work" else 65,
            "priority": 55,
            "tags": item.get("tags"),
            "attributes": {"worth_diary": item.get("worth_diary", False), "worth_proactive": item.get("worth_proactive", False), "generated_by": "life_rhythm_decomposer"},
            "resource_costs": item.get("resource_costs") or {},
            "schedule": {"start": item["start"], "end": item["end"], "block_type": "planned_event", "timezone_name": tz},
            "weight": 1.0,
        })
    return out


def is_abstract_goal_event(event: dict[str, Any], goal: dict[str, Any] | None = None) -> bool:
    title = str(event.get("title") or "")
    etype = str(event.get("event_type") or "")
    gtype = str((goal or {}).get("goal_type") or "")
    gtitle = str((goal or {}).get("title") or "")
    hay = " ".join([title, etype, gtype, gtitle]).lower()
    return ("推进目标" in title or "goal" in hay or etype in {"self_reflection", "lifestyle"}) and any(k in hay for k in ["daily", "life", "日常", "生活", "continuity", "归明观", "委托"])


def list_paper_notes(conn, agent_id: str, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT * FROM proactive_intents WHERE agent_id=? AND status IN ('generated','queued')
              ORDER BY created_at DESC LIMIT ?""",
        (agent_id, int(limit)),
    ).fetchall()
    notes = []
    for r in rows:
        d = dict(r)
        d["delivery_policy"] = loads(d.pop("delivery_policy_json", "{}"), {})
        notes.append({
            "id": d.get("id"),
            "when": d.get("created_at"),
            "kind": d.get("intent_type"),
            "tone": d.get("emotional_tone") or "calm",
            "summary": d.get("summary"),
            "why": d.get("trigger_event_id") or d.get("trigger_result_id") or "self_life",
            "will_interrupt": False,
            "suggested_send": "pending_only：先放在小纸条箱，不主动打扰。",
            "status": d.get("status"),
            "privacy": d.get("privacy_level"),
        })
    lines = ["Proactive 小纸条", "================"]
    if not notes:
        lines.append("目前没有待分享的小纸条。")
    for i, n in enumerate(notes, 1):
        lines.append(f"{i}. {n['when']} · {n['kind']} · {n['tone']} — {n['summary']}")
        lines.append(f"   为什么想说：{n['why']}；打扰风险：{'会' if n['will_interrupt'] else '低'}；建议：{n['suggested_send']}")
    return {"ok": True, "notes": notes, "rendered": "\n".join(lines)}


def diary_draft_content(conn, owner_kind: str, owner_id: str) -> str:
    events = conn.execute(
        """SELECT title,status,event_category,event_type,updated_at FROM events
              WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT 8""",
        (owner_kind, owner_id),
    ).fetchall()
    dreams = conn.execute(
        """SELECT summary, created_at FROM dream_entries WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 2""",
        (owner_kind, owner_id),
    ).fetchall()
    lines = ["今天的内部日记草稿："]
    if events:
        lines.append("最近生活事件：" + "；".join([f"{r['title']}({r['status']})" for r in events[:5]]))
    else:
        lines.append("今天还没有足够的已提交生活事件。")
    if dreams:
        lines.append("梦境余韵：" + "；".join([(r["summary"] or "梦境片段")[:60] for r in dreams]))
    lines.append("这只是内部草稿，不主动发给用户。")
    return "\n".join(lines)
