"""Human-friendly schedule timeline and event/schedule relationship views.

Important semantics:
- Event.status = ``planned`` means the agent has decided the thing exists, but
  it may still be unscheduled.
- ScheduleBlock.status = ``planned`` means a concrete time block has been
  reserved/arranged.  Human UI renders this as “已排期”.
- Event.status = ``scheduled`` means there is at least one active future/current
  ScheduleBlock for the event.

This module is intentionally read-only and human-facing.  It turns the internal
schedule_blocks + events + sleep/session state into simple rows that a person can
read without understanding LifeEngine internals.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from .jsonutil import loads
from .time_utils import parse_datetime


DEFAULT_TZ = "Asia/Tokyo"
ACTIVE_BLOCK_STATUSES = {"planned", "locked", "ready", "in_progress"}
DONE_BLOCK_STATUSES = {"completed", "cancelled", "missed", "skipped", "released", "rescheduled"}
ACTIVE_EVENT_STATUSES = {"planned", "scheduled", "ready", "in_progress", "partial", "postponed", "rescheduled"}
TERMINAL_EVENT_STATUSES = {"completed", "cancelled", "failed", "abandoned", "archived"}

BLOCK_STATUS_LABELS = {
    "planned": "已排期",
    "locked": "已锁定",
    "ready": "待执行",
    "in_progress": "进行中",
    "completed": "已完成",
    "partial": "部分完成",
    "missed": "错过",
    "skipped": "跳过",
    "cancelled": "已取消",
    "rescheduled": "已改期",
    "released": "已释放",
}

EVENT_STATUS_LABELS = {
    "draft": "草案",
    "planned": "计划中/待排期",
    "scheduled": "已排期",
    "ready": "待执行",
    "in_progress": "进行中",
    "partial": "部分完成",
    "postponed": "已推迟",
    "rescheduled": "已改期",
    "completed": "已完成",
    "cancelled": "已取消",
    "failed": "失败",
    "abandoned": "已放弃",
    "archived": "已归档",
}

CATEGORY_LABELS = {
    "sleep": "睡眠",
    "work": "工作",
    "study": "学习",
    "health": "健康",
    "meal": "饮食",
    "purchase": "购物",
    "social": "社交",
    "leisure": "休闲",
    "maintenance": "维护",
    "travel": "出行",
    "creative": "创作",
    "finance": "财务",
    "relationship": "关系",
    "reflection": "复盘",
    "dream": "梦",
    "system": "系统",
    "other": "其他",
}


def _tz_from_canon(canon: dict[str, Any] | None) -> str:
    if not isinstance(canon, dict):
        return DEFAULT_TZ
    truth = canon.get("truth_sources") or {}
    bindings = truth.get("bindings") or {}
    time_binding = bindings.get("time") or bindings.get("clock") or {}
    for key in ("timezone", "tz", "value"):
        val = time_binding.get(key) if isinstance(time_binding, dict) else None
        if isinstance(val, str) and val:
            if val.upper() == "JST":
                return "Asia/Tokyo"
            return val
    rules = canon.get("schedule_rules") or {}
    val = rules.get("timezone") or rules.get("time_zone")
    return str(val) if val else DEFAULT_TZ


def _day_bounds(day: str | None = None, *, period: str = "today", tz_name: str = DEFAULT_TZ) -> tuple[int, int, str, str]:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    p = (period or "today").strip().lower()
    if day:
        base = datetime.fromisoformat(day).replace(tzinfo=tz) if "T" not in day else parse_datetime(day, default_tz=tz_name).astimezone(tz)  # type: ignore[union-attr]
    elif p in {"tomorrow", "明天"}:
        base = now + timedelta(days=1)
    else:
        base = now
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    if p in {"week", "this_week", "一周", "本周"}:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    else:
        end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()), start.date().isoformat(), end.date().isoformat()


def _short_time(value: str | None, ts: int | None = None, *, tz_name: str = DEFAULT_TZ) -> str:
    tz = ZoneInfo(tz_name)
    try:
        if ts is not None:
            return datetime.fromtimestamp(int(ts), tz).strftime("%m-%d %H:%M")
        if value:
            return parse_datetime(value, default_tz=tz_name).astimezone(tz).strftime("%m-%d %H:%M")  # type: ignore[union-attr]
    except Exception:
        pass
    return "--:--"


def _time_range(row: dict[str, Any], *, tz_name: str = DEFAULT_TZ) -> str:
    return f"{_short_time(row.get('start'), row.get('start_ts'), tz_name=tz_name)} - {_short_time(row.get('end'), row.get('end_ts'), tz_name=tz_name)}"


def _block_label(status: str | None) -> str:
    return BLOCK_STATUS_LABELS.get(str(status or ""), str(status or "-"))


def _event_label(status: str | None) -> str:
    return EVENT_STATUS_LABELS.get(str(status or ""), str(status or "-"))


def _cat_label(cat: str | None) -> str:
    return CATEGORY_LABELS.get(str(cat or ""), str(cat or "-"))


def _derive_schedule_state(event_status: str | None, block_status: str | None, start_ts: int | None, end_ts: int | None, *, now_ts: int | None = None) -> str:
    now_ts = now_ts or int(datetime.now().timestamp())
    bs = str(block_status or "")
    es = str(event_status or "")
    if es in TERMINAL_EVENT_STATUSES or bs in {"completed", "cancelled", "missed", "skipped"}:
        return "closed"
    if bs in {"in_progress"} or (start_ts and end_ts and start_ts <= now_ts <= end_ts and bs in ACTIVE_BLOCK_STATUSES):
        return "active_now"
    if bs in ACTIVE_BLOCK_STATUSES:
        return "scheduled"
    if es in {"planned", "postponed", "rescheduled"}:
        return "unscheduled_or_waiting"
    return "unknown"


def list_schedule(conn, owner_kind: str, owner_id: str, *, period: str = "today", date: str | None = None,
                  start: str | None = None, end: str | None = None, tz_name: str = DEFAULT_TZ,
                  include_completed: bool = True, limit: int = 200) -> dict[str, Any]:
    """Return machine-readable and human-readable schedule rows."""
    if start and end:
        sdt = parse_datetime(start, default_tz=tz_name)
        edt = parse_datetime(end, default_tz=tz_name)
        if sdt is None or edt is None or edt <= sdt:
            raise ValueError("schedule end must be after start")
        start_ts, end_ts = int(sdt.timestamp()), int(edt.timestamp())
        label_start, label_end = sdt.date().isoformat(), edt.date().isoformat()
    else:
        start_ts, end_ts, label_start, label_end = _day_bounds(date, period=period, tz_name=tz_name)

    statuses = () if include_completed else tuple(ACTIVE_BLOCK_STATUSES | {"rescheduled"})
    status_filter = "" if include_completed else "AND b.status IN ({})".format(",".join("?" for _ in statuses))
    params: list[Any] = [owner_kind, owner_id, end_ts, start_ts]
    if statuses:
        params.extend(statuses)
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT b.*, e.title AS event_title, e.event_type, e.event_category, e.activity_domain,
               e.subtype, e.status AS event_status, e.importance, e.priority, e.progress,
               e.actual_start AS event_actual_start, e.actual_end AS event_actual_end,
               e.tags_json, e.location_json, e.interruptibility_json AS event_interruptibility_json
          FROM schedule_blocks b
          LEFT JOIN events e ON e.id=b.event_id
         WHERE b.owner_kind=? AND b.owner_id=?
           AND COALESCE(b.start_ts, CAST(strftime('%s', b.start) AS INTEGER)) < ?
           AND COALESCE(b.end_ts, CAST(strftime('%s', b.end) AS INTEGER)) > ?
           {status_filter}
         ORDER BY COALESCE(b.start_ts, CAST(strftime('%s', b.start) AS INTEGER)), b.created_at
         LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    items: list[dict[str, Any]] = []
    now_ts = int(datetime.now().timestamp())
    for r in rows:
        d = dict(r)
        d["tags"] = loads(d.pop("tags_json", "[]"), [])
        d["location"] = loads(d.pop("location_json", "{}"), {})
        d["interruptibility"] = loads(d.pop("event_interruptibility_json", "{}"), {})
        d["time_range"] = _time_range(d, tz_name=tz_name)
        d["title"] = d.get("event_title") or d.get("block_type") or "未命名时间块"
        d["block_status_label"] = _block_label(d.get("status"))
        d["event_status_label"] = _event_label(d.get("event_status"))
        d["category_label"] = _cat_label(d.get("event_category") or d.get("event_type") or d.get("block_type"))
        d["schedule_semantic_state"] = _derive_schedule_state(d.get("event_status"), d.get("status"), d.get("start_ts"), d.get("end_ts"), now_ts=now_ts)
        items.append(d)
    summary = {
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "period": period or "today",
        "date": date,
        "start_date": label_start,
        "end_date": label_end,
        "tz": tz_name,
        "count": len(items),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "semantics": {
            "event_planned": "计划中/待排期：事情已经存在，但不等于已经安排时间。",
            "event_scheduled": "已排期：事件至少有一个 active ScheduleBlock。",
            "schedule_block_planned": "已排期：这段时间已经被预留给 event/action。",
            "relationship": "Event 是事情；ScheduleBlock 是时间块；一个 Event 可以有多个 ScheduleBlock。",
        },
    }
    return {"ok": True, "summary": summary, "items": items, "rendered": render_schedule(summary, items)}


def list_unscheduled_events(conn, owner_kind: str, owner_id: str, *, limit: int = 100) -> dict[str, Any]:
    """Return events that exist as plans but do not currently own an active schedule block."""
    rows = conn.execute(
        f"""
        SELECT e.*
          FROM events e
         WHERE e.owner_kind=? AND e.owner_id=?
           AND e.status IN ({','.join('?' for _ in ACTIVE_EVENT_STATUSES)})
           AND NOT EXISTS (
                SELECT 1 FROM schedule_blocks b
                 WHERE b.event_id=e.id AND b.owner_kind=e.owner_kind AND b.owner_id=e.owner_id
                   AND b.status IN ({','.join('?' for _ in ACTIVE_BLOCK_STATUSES)})
           )
         ORDER BY e.importance DESC, e.priority DESC, e.updated_at DESC
         LIMIT ?
        """,
        (owner_kind, owner_id, *tuple(ACTIVE_EVENT_STATUSES), *tuple(ACTIVE_BLOCK_STATUSES), int(limit)),
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        for key, default in [("tags_json", []), ("attributes_json", {}), ("location_json", {}), ("interruptibility_json", {})]:
            if key in d:
                d[key[:-5] if key.endswith("_json") else key] = loads(d.get(key), default)
        d["event_status_label"] = _event_label(d.get("status"))
        d["category_label"] = _cat_label(d.get("event_category") or d.get("event_type"))
        items.append(d)
    out = {"ok": True, "count": len(items), "items": items}
    out["rendered"] = render_unscheduled(items)
    return out


def render_unscheduled(items: list[dict[str, Any]]) -> str:
    lines = ["计划中但尚未排期的事项", "===================="]
    if not items:
        lines.append("没有待排期事项。")
        return "\n".join(lines)
    for i, it in enumerate(items, start=1):
        lines.append(f"{i}. {it.get('title') or '未命名事项'}")
        lines.append(f"   类型：{it.get('category_label')}; 状态：{it.get('event_status_label')}; 重要度：{it.get('importance') or 0}")
        if it.get("planned_start") or it.get("planned_end"):
            lines.append(f"   原计划：{it.get('planned_start') or '-'} → {it.get('planned_end') or '-'}")
        lines.append("   说明：这是 Event 计划，不是已排期；需要 Scheduler 创建 ScheduleBlock 才会出现在日程时间线。")
    lines.append("")
    lines.append("Agent 可通过 life_event(action='schedule') 或 LifeOps 创建 ScheduleBlock 来真正排期。")
    return "\n".join(lines)


def render_schedule(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    period = summary.get("period") or "today"
    if period in {"week", "this_week", "一周", "本周"}:
        title = f"这一周的日程（{summary.get('start_date')} - {summary.get('end_date')}）"
    elif period in {"tomorrow", "明天"}:
        title = f"明天的日程（{summary.get('start_date')}）"
    else:
        title = f"今天的日程（{summary.get('start_date')}）" if not summary.get("date") else f"{summary.get('date')} 的日程"
    lines = [title, "=" * len(title)]
    lines.append("说明：Event=事情；ScheduleBlock=时间块。Event『计划中』不等于已排期；时间块『已排期』才表示已占用时间。")
    if not items:
        lines.append("目前没有排定的日程。")
        lines.append("可以看：/life schedule unscheduled，查看计划中但尚未排期的事项。")
        return "\n".join(lines)

    last_day = None
    for i, it in enumerate(items, start=1):
        start_label = _short_time(it.get("start"), it.get("start_ts"), tz_name=summary.get("tz") or DEFAULT_TZ)
        day_label = start_label.split()[0] if " " in start_label else summary.get("start_date")
        if period in {"week", "this_week", "一周", "本周"} and day_label != last_day:
            lines.append("")
            lines.append(f"[{day_label}]")
            last_day = day_label
        title = it.get("title") or "未命名事项"
        category = it.get("category_label") or _cat_label(it.get("event_category") or it.get("event_type") or it.get("block_type"))
        block_status = it.get("block_status_label") or _block_label(it.get("status"))
        event_status = it.get("event_status_label") or _event_label(it.get("event_status"))
        actual = ""
        if it.get("actual_start") or it.get("actual_end"):
            actual = f"；实际：{_short_time(it.get('actual_start'), it.get('actual_start_ts'), tz_name=summary.get('tz') or DEFAULT_TZ)} - {_short_time(it.get('actual_end'), it.get('actual_end_ts'), tz_name=summary.get('tz') or DEFAULT_TZ)}"
        location = it.get("location") or {}
        loc_txt = f"；地点：{location.get('name') or location.get('label')}" if isinstance(location, dict) and (location.get('name') or location.get('label')) else ""
        intr = it.get("interruptibility") or {}
        intr_txt = f"；可打断：{intr.get('level')}" if isinstance(intr, dict) and intr.get("level") else ""
        semantic = it.get("schedule_semantic_state") or "scheduled"
        lines.append(f"{i}. {it.get('time_range')}  {title}")
        lines.append(f"   类型：{category}；排期：{block_status}；事件：{event_status}；关系：{semantic}{actual}{loc_txt}{intr_txt}")
    lines.append("")
    lines.append("常用：/life schedule；/life schedule tomorrow；/life schedule week；/life schedule 2026-06-11；/life schedule unscheduled")
    return "\n".join(lines)


def explain_schedule_semantics() -> dict[str, Any]:
    rendered = """Event / Schedule 关系说明
=======================
1. Event 是“事情”：比如买裙子、复习、睡觉、小单子。
2. ScheduleBlock 是“时间块”：比如今天 10:30-12:00 做这个事情。
3. 一个 Event 可以有多个 ScheduleBlock：多次执行、拆分执行、推迟、改期都会出现多个时间块。
4. Event.status=planned 只表示事情已经被计划/存在，通常还没有真正占用时间。
5. Event.status=scheduled 表示已经有 active ScheduleBlock。
6. ScheduleBlock.status=planned 在人类界面里显示为“已排期”，因为它已经有明确 start/end。
7. 真正排期发生在 Scheduler/LifeOps 创建 ScheduleBlock 并绑定 event_id 时。
8. 睡眠也是 Event + ScheduleBlock，但实际睡眠由 SleepSession 记录，所以计划睡眠和实际睡眠可以不一致。
"""
    return {"ok": True, "rendered": rendered, "semantics": {
        "event": "thing/intention/lifecycle",
        "schedule_block": "time reservation/execution window",
        "planned_event": "exists but may be unscheduled",
        "scheduled_event": "has active schedule block",
        "planned_schedule_block": "concrete time block reserved",
        "relationship": "one Event to many ScheduleBlocks",
    }}
