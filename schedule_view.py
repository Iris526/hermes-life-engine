"""Human-friendly schedule timeline views for LifeEngine.

This module is intentionally read-only and human-facing. It turns the internal
schedule_blocks + events + sleep/session state into simple timeline rows that a
person can read without understanding LifeEngine internals.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from .jsonutil import loads
from .time_utils import parse_datetime


DEFAULT_TZ = "Asia/Tokyo"


def _tz_from_canon(canon: dict[str, Any] | None) -> str:
    if not isinstance(canon, dict):
        return DEFAULT_TZ
    # Accept several Canon shapes; LifeEngine is intentionally tolerant here.
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
        # ISO-style week, Monday through next Monday.
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

    statuses = () if include_completed else ("planned", "locked", "ready", "in_progress", "rescheduled")
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
    for r in rows:
        d = dict(r)
        d["tags"] = loads(d.pop("tags_json", "[]"), [])
        d["location"] = loads(d.pop("location_json", "{}"), {})
        d["interruptibility"] = loads(d.pop("event_interruptibility_json", "{}"), {})
        d["time_range"] = _time_range(d, tz_name=tz_name)
        d["title"] = d.get("event_title") or d.get("block_type") or "未命名时间块"
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
    }
    return {"ok": True, "summary": summary, "items": items, "rendered": render_schedule(summary, items)}


def render_schedule(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    period = summary.get("period") or "today"
    if period in {"week", "this_week", "一周", "本周"}:
        title = f"这一周的日程（{summary.get('start_date')} - {summary.get('end_date')}）"
    elif period in {"tomorrow", "明天"}:
        title = f"明天的日程（{summary.get('start_date')}）"
    else:
        title = f"今天的日程（{summary.get('start_date')}）" if not summary.get("date") else f"{summary.get('date')} 的日程"
    lines = [title, "=" * len(title)]
    if not items:
        lines.append("目前没有排定的日程。")
        lines.append("可以说：/life setup 补充生活设定，或让 Agent 自己通过 autonomy 安排今天。")
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
        category = it.get("event_category") or it.get("event_type") or it.get("block_type")
        status = it.get("status")
        event_status = it.get("event_status")
        actual = ""
        if it.get("actual_start") or it.get("actual_end"):
            actual = f"；实际：{_short_time(it.get('actual_start'), it.get('actual_start_ts'), tz_name=summary.get('tz') or DEFAULT_TZ)} - {_short_time(it.get('actual_end'), it.get('actual_end_ts'), tz_name=summary.get('tz') or DEFAULT_TZ)}"
        location = it.get("location") or {}
        loc_txt = f"；地点：{location.get('name') or location.get('label')}" if isinstance(location, dict) and (location.get('name') or location.get('label')) else ""
        intr = it.get("interruptibility") or {}
        intr_txt = f"；可打断：{intr.get('level')}" if isinstance(intr, dict) and intr.get("level") else ""
        lines.append(f"{i}. {it.get('time_range')}  {title}")
        lines.append(f"   类型：{category}；时间块：{status}；事件：{event_status or '-'}{actual}{loc_txt}{intr_txt}")
    lines.append("")
    lines.append("常用：/life schedule 今天；/life schedule 明天；/life schedule week；/life schedule 2026-06-11")
    return "\n".join(lines)
