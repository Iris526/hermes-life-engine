"""Time normalization utilities for LifeEngine.

All user/model-facing times may remain ISO-8601 strings, but all scheduler,
overlap, and heartbeat comparisons should use epoch seconds. This avoids bugs
when one value is stored as `+09:00` and another as `Z`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_datetime(value: str | None, *, default_tz: str = "UTC") -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("datetime value must be an ISO-8601 string")
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 datetime: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(default_tz))
    return dt


def to_epoch(value: str | None, *, default_tz: str = "UTC") -> int | None:
    dt = parse_datetime(value, default_tz=default_tz)
    if dt is None:
        return None
    return int(dt.timestamp())


def normalized_iso(value: str | None, *, default_tz: str = "UTC") -> str | None:
    dt = parse_datetime(value, default_tz=default_tz)
    if dt is None:
        return None
    return dt.isoformat()


def normalize_range(start: str | None, end: str | None, *, default_tz: str = "UTC") -> tuple[str | None, str | None, int | None, int | None]:
    start_iso = normalized_iso(start, default_tz=default_tz)
    end_iso = normalized_iso(end, default_tz=default_tz)
    start_ts = to_epoch(start_iso, default_tz=default_tz)
    end_ts = to_epoch(end_iso, default_tz=default_tz)
    if start_ts is not None and end_ts is not None and end_ts <= start_ts:
        raise ValueError("end time must be after start time")
    return start_iso, end_iso, start_ts, end_ts
