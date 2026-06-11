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
