"""Human-friendly required-setting checks for LifeEngine startup/setup.

These checks are advisory for humans and internal guidance for the agent. They
make sure the Life Canon has the minimum anchors needed for a coherent self-life:
identity, worldview, time, weather/truth source, sleep, resources, and autonomy.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_audit, new_id

REQUIRED_SETTING_KEYS = [
    "identity",
    "worldview",
    "time",
    "weather",
    "truth_sources",
    "sleep",
    "resources",
    "autonomy",
]


def _has_any(d: dict[str, Any], *keys: str) -> bool:
    return any(bool(d.get(k)) for k in keys)


def check_required_settings(conn, owner_kind: str, owner_id: str, canon: dict[str, Any], *, persist: bool = True, source: str = "startup") -> dict[str, Any]:
    identity = canon.get("identity") or {}
    worldview = canon.get("worldview") or {}
    truth = canon.get("truth_sources") or {}
    bindings = truth.get("bindings") or {}
    resources = ((canon.get("resources") or {}).get("definitions") or {})
    sleep = canon.get("sleep") or {}
    autonomy = canon.get("autonomy") or {}

    items: list[dict[str, Any]] = []
    def item(key: str, ok: bool, title: str, message: str, suggestion: str) -> None:
        items.append({"key": key, "ok": bool(ok), "title": title, "message": message, "suggestion": suggestion})

    item("identity", _has_any(identity, "name", "selfDescription", "self_description", "raw_identity_description"),
         "人设 / 身份", "需要知道 Agent 是谁。", "例如：你叫澪，是 23 岁，住在和我同城。")
    item("worldview", _has_any(worldview, "raw_world_description", "world_binding", "worldType", "world_type", "physicalRules"),
         "世界观", "需要知道 Agent 生活在哪种世界。", "例如：你生活在现实世界，和我同城；或者生活在虚拟海边小城。")
    item("time", bool(bindings.get("time") or bindings.get("clock") or (canon.get("schedule_rules") or {}).get("timezone")),
         "时间设定", "需要确定时间、时区、时间流速。", "例如：时间使用 Asia/Tokyo，和真实时间同步。")
    weather = bindings.get("weather") or {}
    item("weather", bool(weather and weather.get("authority")),
         "天气设定", "需要知道天气来源。", "例如：天气跟用户所在地一致；或使用随机虚拟天气。")
    item("truth_sources", bool(bindings),
         "真相源", "需要知道哪些外部/虚拟事实以什么来源为准。", "例如：天气=user_current_location；货币=JPY；地点=fixed_setting。")
    item("sleep", bool(sleep and (sleep.get("target_minutes") or sleep.get("defaultSleepHours") or sleep.get("core_sleep_required") is not None)),
         "睡眠规则", "需要知道 Agent 是否需要睡觉、目标睡眠多久、是否允许通宵。", "例如：每天目标睡眠 7.5 小时，允许被 call 叫醒。")
    item("resources", bool(resources),
         "资源定义", "至少需要几个核心资源，才能做资源闭环。", "例如：energy、focus、mood、fatigue、sleep_debt_minutes、money.jpy。")
    item("autonomy", bool(autonomy) or True,
         "自治规则", "需要确定 Agent 是否可以自己推进生活。", "默认：允许 Agent 自主管理自己的生活和安全 review。")

    missing = [x for x in items if not x["ok"]]
    status = "ok" if not missing else "needs_setup"
    out = {"ok": not missing, "status": status, "missing_count": len(missing), "items": items, "missing": missing}
    if persist:
        check_id = new_id("settingscheck")
        conn.execute(
            """INSERT INTO life_required_setting_checks(id, owner_kind, owner_id, status, missing_count, items_json, source)
                 VALUES(?,?,?,?,?,?,?)""",
            (check_id, owner_kind, owner_id, status, len(missing), dumps(items), source),
        )
        out["id"] = check_id
        if missing:
            append_audit(conn, owner_kind, owner_id, "required_settings_check", "warning", f"LifeEngine missing {len(missing)} required setting(s)", {"check_id": check_id, "missing": [m["key"] for m in missing]})
    out["rendered"] = render_required_settings(out)
    return out


def latest_required_settings_check(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM life_required_setting_checks WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1", (owner_kind, owner_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["items"] = loads(d.pop("items_json"), [])
    d["missing"] = [x for x in d["items"] if not x.get("ok")]
    d["ok"] = not d["missing"]
    d["rendered"] = render_required_settings(d)
    return d


def render_required_settings(check: dict[str, Any]) -> str:
    lines = ["LifeEngine 必选设定检查", "======================="]
    if check.get("ok"):
        lines.append("状态：已满足。")
    else:
        lines.append(f"状态：还缺 {check.get('missing_count', len(check.get('missing') or []))} 项。")
    for it in check.get("items") or []:
        mark = "✓" if it.get("ok") else "!"
        lines.append(f"{mark} {it.get('title')}：{'已设置' if it.get('ok') else it.get('message')}")
        if not it.get("ok"):
            lines.append(f"  建议：{it.get('suggestion')}")
    lines.append("")
    lines.append("可用 /life setup <自然语言设定> 补充；/life commit 提交。支持真实来源，也支持自定义虚拟规则，例如随机天气。")
    return "\n".join(lines)


def required_settings_spec() -> dict[str, Any]:
    """Return a stable, human/agent-readable required-setting specification."""
    spec = {
        "identity": {
            "title": "人设 / 身份",
            "required": True,
            "examples": ["你叫澪，是 23 岁，住在和我同城。", "你是虚拟第七城的符咒修补师。"],
            "paths": ["identity.name", "identity.selfDescription", "identity.occupation", "identity.homeLocationPolicy"],
        },
        "worldview": {
            "title": "世界观",
            "required": True,
            "examples": ["现实绑定世界，时间和用户同步。", "虚拟城市，天气按叙事模拟器生成。"],
            "paths": ["worldview.world_type", "worldview.raw_world_description", "worldview.physicalRules"],
        },
        "time": {
            "title": "时间设定",
            "required": True,
            "real_sources": ["system_clock", "fixed_setting"],
            "virtual_sources": ["custom_time_rule"],
            "paths": ["truth_sources.bindings.time", "schedule_rules.timezone"],
        },
        "weather": {
            "title": "天气设定",
            "required": True,
            "real_sources": ["user_current_location", "external_tool", "fixed_setting"],
            "virtual_sources": ["narrative_simulator", "random_weather", "custom_world_rule"],
            "paths": ["truth_sources.bindings.weather"],
        },
        "resources": {
            "title": "资源定义",
            "required": True,
            "suggested_keys": ["energy", "focus", "mood", "fatigue", "sleep_debt_minutes", "money.jpy"],
            "paths": ["resources.definitions"],
        },
        "sleep": {
            "title": "睡眠规则",
            "required": True,
            "paths": ["sleep.target_minutes", "sleep.allow_all_nighter", "sleep.core_sleep_required"],
        },
        "autonomy": {
            "title": "自治规则",
            "required": True,
            "default": "Agent self-life autonomy and safe managed review are enabled by default.",
            "paths": ["autonomy.enabled", "autonomy.mode"],
        },
    }
    lines = ["LifeEngine 必选设定规格", "===================="]
    for key, meta in spec.items():
        lines.append(f"- {key}: {meta['title']}")
        if meta.get("examples"):
            lines.append(f"  示例：{meta['examples'][0]}")
        if meta.get("real_sources"):
            lines.append(f"  真实来源：{', '.join(meta['real_sources'])}")
        if meta.get("virtual_sources"):
            lines.append(f"  虚拟规则：{', '.join(meta['virtual_sources'])}")
    return {"ok": True, "spec": spec, "rendered": "\n".join(lines)}


def default_setting_patch(kind: str = "balanced") -> dict[str, Any]:
    """Conservative default CanonDraft patch.  It is a draft, not active Canon."""
    kind = (kind or "balanced").strip().lower()
    weather_authority = "user_current_location"
    world_type = "real_world_bound"
    world_text = "现实绑定世界：时间与真实时间同步，天气默认参考用户所在地。"
    if kind in {"virtual", "virtual_random", "fictional", "random_weather", "虚拟"}:
        weather_authority = "narrative_simulator"
        world_type = "semi_real_narrative"
        world_text = "半真实叙事世界：时间与真实时间同步，天气和环境可由叙事模拟器生成。"
    return {
        "identity": {
            "name": "未命名 Agent",
            "selfDescription": "一个拥有 LifeEngine 自我生活状态的 Agent。请继续补充更具体的人设。",
        },
        "worldview": {
            "world_type": world_type,
            "raw_world_description": world_text,
            "physicalRules": {"needsSleep": True, "needsFood": True, "hasPhysicalBody": True, "timeFlow": "real_time"},
        },
        "truth_sources": {
            "bindings": {
                "time": {"domain": "time", "authority": "system_clock", "timezone": "Asia/Tokyo", "fallback": "unknown"},
                "weather": {"domain": "weather", "authority": weather_authority, "freshness_ttl_minutes": 120, "fallback": "unknown"},
                "currency": {"domain": "currency", "authority": "fixed_setting", "value": "JPY"},
            }
        },
        "schedule_rules": {"timezone": "Asia/Tokyo"},
        "sleep": {"target_minutes": 450, "core_sleep_required": True, "allow_all_nighter": True, "interruptible_by_call": True},
        "resources": {
            "definitions": {
                "energy": {"display_name": "精力", "resource_class": "capacity", "unit": "points", "initial": 60, "min": 0, "max": 100},
                "focus": {"display_name": "专注", "resource_class": "capacity", "unit": "points", "initial": 60, "min": 0, "max": 100},
                "mood": {"display_name": "心情", "resource_class": "state", "unit": "points", "initial": 50, "min": -100, "max": 100},
                "fatigue": {"display_name": "疲劳", "resource_class": "state", "unit": "points", "initial": 20, "min": 0, "max": 100},
                "sleep_debt_minutes": {"display_name": "睡眠债", "resource_class": "state", "unit": "minutes", "initial": 0, "min": 0, "max": 1440},
                "money.jpy": {"display_name": "钱包（日元）", "resource_class": "fungible", "unit": "JPY", "initial": 0, "min": 0},
            }
        },
        "autonomy": {"enabled": True, "mode": "full", "managed_review_loop": "auto"},
    }


def default_setting_suggestions(check: dict[str, Any], kind: str = "balanced") -> dict[str, Any]:
    patch = default_setting_patch(kind)
    missing_keys = [m.get("key") for m in check.get("missing") or []]
    # Keep the full patch by default; it is a draft so it is safe and easy for
    # the Agent/human to inspect before /life commit.
    lines = ["LifeEngine 设定补全建议", "======================"]
    lines.append("以下建议只会写入 CanonDraft，不会直接启用。启用必须 /life commit。")
    if missing_keys:
        lines.append("缺失项：" + "、".join(str(x) for x in missing_keys))
    else:
        lines.append("当前没有缺失必选项；也可以用这些模板微调设定。")
    lines.append("可选模板：balanced / virtual_random。")
    return {"ok": True, "kind": kind, "missing_keys": missing_keys, "patch": patch, "rendered": "\n".join(lines)}
