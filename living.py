"""LifeEngine living layer: Canon consistency, day rhythm, concrete-life presets.

v0.12.5 turns the mature LifeEngine runtime from a state/event manager toward a
self-life simulator.  This module deliberately stays above raw SQL and below the
Hermes UI: it produces human-readable reports and LifeOps plans that the runtime
can commit through the normal validator / journal / receipt path.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from .canon import canon_skin_name as _shared_canon_skin_name, get_active_canon
from .jsonutil import dumps, loads
from .skins import DEFAULT_LEGACY_LIVING_SKIN, get_canon_skin
from .trace import append_audit, append_journal, new_id
from .time_utils import now_iso


def _norm_tz(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    text = v.strip()
    aliases = {"CST_CN": "Asia/Shanghai"}
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
            issue("weather_virtual_rule_missing", "info", "虚拟天气缺少规则", "天气使用叙事/随机模拟，但没有 mode 或 rules。", "例如：mode=random_local 或 rules=符合当前世界季节。", ["truth_sources.bindings.weather"])

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


CANON_LIVING_PRESET_NAME = "canon"
EMPTY_LIVING_PRESET_NAME = "none"


def canon_with_default_living_skin(canon: dict[str, Any] | None) -> dict[str, Any]:
    """为旧默认 agent 兼容路径补一个 skin 引用。

    输入是已经读取出的 active Canon 或默认 Canon 拷贝；输出是新的 Canon dict。
    调用方式为 runtime 在“默认 agent 尚无 active Canon”的旧 living 动作中同步调用。
    调用方包括 init_resources/day_rhythm/decompose_abstract 的兼容入口；副作用为无，
    不写数据库、不修改 DEFAULT_CANON_TEMPLATE。失败处理是保留原 Canon，避免未知
    agent 因缺少 skin 被回退到角色内容。
    """
    data = deepcopy(canon or {})
    living = data.setdefault("living", {})
    if isinstance(living, dict) and not living.get("skin"):
        living["skin"] = DEFAULT_LEGACY_LIVING_SKIN
    return data


def _canon_living(canon: dict[str, Any] | None) -> dict[str, Any]:
    """读取 Canon 的 living 块。

    输入是任意 Canon-like dict；输出是 living dict 或空 dict。调用方式为本模块
    解析资源、供给、节律和 summary 时同步调用。函数无副作用；非 dict 或缺失
    living 时按空块处理，避免隐式角色回退。
    """
    data = canon if isinstance(canon, dict) else {}
    living = data.get("living") if isinstance(data.get("living"), dict) else {}
    return living or {}


def _canon_skin_name(canon: dict[str, Any] | None, preset: str | None = None) -> str | None:
    """解析本次 living 读取应使用的 skin 名。

    输入是 active Canon 与可选显式 preset；输出是 skin 名或 None。调用方式为
    skin 查找与 run-log 命名同步调用。函数不校验 skin 是否存在、不写状态；
    未声明时返回 None，让调用方保持空结果。
    """
    if isinstance(preset, str) and preset.strip():
        return preset.strip()
    return _shared_canon_skin_name(canon)


def _skin_data(canon: dict[str, Any] | None, preset: str | None = None) -> dict[str, Any]:
    """读取 Canon 指向的 skin 数据。

    输入是 active Canon 与可选显式 preset；输出是 skin 数据深拷贝或空 dict。
    调用方式为资源、供给、节律和 summary 解析同步调用。函数无副作用；未知
    skin 由 get_canon_skin 统一降级为空数据。
    """
    return get_canon_skin(_canon_skin_name(canon, preset))


def _canon_resource_definitions(canon: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把 Canon resources 转成 RESOURCE_DEFINE payload 列表。

    输入是 active Canon；输出是规范化后的资源定义 payload 列表。调用方式为
    init_resources 在没有 skin 资源时同步调用。函数无副作用；它只读取 Canon
    中已有 resources.presets 或 resources.definitions，并兼容 min/max 字段名。
    """
    data = canon if isinstance(canon, dict) else {}
    resources = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    presets = resources.get("presets") if isinstance(resources.get("presets"), dict) else {}
    for key in ("living", "default"):
        preset = presets.get(key)
        if isinstance(preset, list):
            return [deepcopy(item) for item in preset if isinstance(item, dict)]
    definitions = resources.get("definitions") if isinstance(resources.get("definitions"), dict) else {}
    out: list[dict[str, Any]] = []
    for key, spec in definitions.items():
        if not isinstance(spec, dict):
            continue
        payload = deepcopy(spec)
        payload.setdefault("key", key)
        if "min_value" not in payload and "min" in payload:
            payload["min_value"] = payload.get("min")
        if "max_value" not in payload and "max" in payload:
            payload["max_value"] = payload.get("max")
        payload.pop("min", None)
        payload.pop("max", None)
        out.append(payload)
    return out


def living_preset_name(canon: dict[str, Any] | None = None, preset: str | None = None) -> str:
    """返回本次 living 解析使用的稳定 preset 标签。

    输入是 active Canon 与可选显式 preset；输出写入 run-log 的非空标签。
    调用方式为 runtime 在生成资源、节律或分解时同步读取；调用方是 living
    tool 与 heartbeat。函数不写库，未知 skin 返回通用标签或空标签，不会
    自动回退到任何角色 skin。
    """
    skin_name = _canon_skin_name(canon, preset)
    if skin_name and get_canon_skin(skin_name):
        return skin_name
    living = _canon_living(canon)
    if living.get("rhythm_templates") or living.get("supplies"):
        return CANON_LIVING_PRESET_NAME
    if _canon_resource_definitions(canon):
        return CANON_LIVING_PRESET_NAME
    return EMPTY_LIVING_PRESET_NAME


def resource_preset_ops(preset: str | None = None, *, canon: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """从 Canon 生成 living 资源初始化 LifeOps。

    输入是 active Canon 和可选显式 skin/preset 名；输出为 RESOURCE_DEFINE ops。
    调用方式为 life_living init_resources 同步调用；业务调用方是 runtime.living。
    副作用为无，真正写库由 commit_ops 完成。未知 skin 或缺少资源定义时返回空
    列表，保证非角色 Canon 不继承任何角色货币或物资资源。
    """
    skin = _skin_data(canon, preset)
    resources = (((skin.get("resources") or {}).get("definitions") or []) if skin else []) or _canon_resource_definitions(canon)
    return [{"type": "RESOURCE_DEFINE", "payload": deepcopy(res)} for res in resources if isinstance(res, dict)]


def supply_items(preset: str | None = None, *, canon: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """从 Canon 读取 living 供给物资定义。

    输入是 active Canon 与可选显式 skin/preset 名；输出是供 collection 层消费的
    supply item 数据。调用方式为测试或后续 collection bootstrap 同步读取；
    当前函数不写 collection、不产生 LifeOps。未知 skin 或缺少 supplies 时返回
    空列表，避免非角色 Canon 继承角色物资。
    """
    skin = _skin_data(canon, preset)
    living = _canon_living(canon)
    items = living.get("supplies") if isinstance(living.get("supplies"), list) else None
    if items is None and skin:
        items = ((skin.get("living") or {}).get("supplies") or [])
    return [deepcopy(item) for item in (items or []) if isinstance(item, dict)]


def _time_for(date_key: str, hhmm: str, tz: str) -> str:
    """按 Canon 时区把日期和 HH:MM 物化为 rhythm 时间字符串。

    输入是日期、模板时间和 IANA 时区名；输出是 LifeOps 现有可接受的 ISO-like
    时间。调用方是 living rhythm 与抽象目标分解；函数不写状态。UTC 沿用旧的
    无 offset 形式，其它有效时区用 ZoneInfo 计算 offset，避免在引擎逻辑里写死
    任一角色时区。
    """
    if str(tz or "").upper() == "UTC":
        return f"{date_key}T{hhmm}:00"
    try:
        local = datetime.fromisoformat(f"{date_key}T{hhmm}:00").replace(tzinfo=ZoneInfo(str(tz)))
        offset = local.strftime("%z")
        if offset:
            return f"{date_key}T{hhmm}:00{offset[:3]}:{offset[3:]}"
    except Exception:
        pass
    return f"{date_key}T{hhmm}:00"


def _template_time(value: Any, date_key: str, tz: str) -> str | None:
    """把 Canon 模板里的时间字段物化为 ISO-like 时间。

    输入是模板时间值、日期和时区；输出是已有绝对时间或按日期拼出的时间字符串。
    调用方式为 rhythm_templates 逐项同步调用。函数无副作用；空值或非字符串返回
    None，由上层跳过该模板。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "T" in text:
        return text
    return _time_for(date_key, text, tz)


def rhythm_templates(date_key: str | None = None, tz: str = "UTC", preset: str | None = None,
                     *, canon: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """从 active Canon 物化当天 living rhythm 模板。

    输入是日期、时区、active Canon 与可选显式 skin/preset 名；输出是带 start/end
    的事件模板列表。调用方式为手动 day_rhythm、heartbeat 和抽象目标分解同步读取。
    函数不写库；未知 skin、无 living.rhythm_templates 或模板时间缺失时返回空列表，
    保证未声明角色内容的 Canon 不生成任何角色节律。
    """
    date_key = date_key or datetime.now(ZoneInfo(tz)).date().isoformat()
    skin = _skin_data(canon, preset)
    living = _canon_living(canon)
    raw_templates = living.get("rhythm_templates") if isinstance(living.get("rhythm_templates"), list) else None
    if raw_templates is None and skin:
        raw_templates = ((skin.get("living") or {}).get("rhythm_templates") or [])
    out: list[dict[str, Any]] = []
    for template in raw_templates or []:
        if not isinstance(template, dict):
            continue
        item = deepcopy(template)
        start = _template_time(item.pop("start_time", item.get("start")), date_key, tz)
        end = _template_time(item.pop("end_time", item.get("end")), date_key, tz)
        if not start or not end:
            continue
        item["start"] = start
        item["end"] = end
        out.append(item)
    return out


def rhythm_proactive_summary(preset: str | None = None, *, canon: dict[str, Any] | None = None) -> str | None:
    """读取 Canon 为 rhythm engine 声明的 proactive summary。

    输入是 active Canon 与可选显式 skin/preset 名；输出是可直接写入 proactive
    intent 的摘要文本或 None。调用方式为 runtime 在已生成 rhythm 且模板标记
    worth_proactive 后同步读取；函数不写库，缺少 summary 时不生成通用替代文案。
    """
    living = _canon_living(canon)
    value = living.get("rhythm_proactive_summary")
    if isinstance(value, str) and value.strip():
        return value.strip()
    skin = _skin_data(canon, preset)
    value = ((skin.get("living") or {}).get("rhythm_proactive_summary") if skin else None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def abstract_goal_children(date_key: str | None = None, tz: str = "UTC", preset: str | None = None,
                           *, canon: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """从 Canon rhythm 模板拆出抽象目标子事件。

    输入是日期、时区、active Canon 与可选显式 skin/preset 名；输出是 DECOMPOSE_EVENT
    使用的 child payload 列表。调用方式为 runtime.living 的 decompose_abstract。
    函数无副作用；无 rhythm 模板时返回空列表，不做角色默认回退。
    """
    out = []
    for item in rhythm_templates(date_key, tz, preset, canon=canon)[:4]:
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
    return ("推进目标" in title or "goal" in hay or etype in {"self_reflection", "lifestyle"}) and any(k in hay for k in ["daily", "life", "日常", "生活", "continuity", "委托"])


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
