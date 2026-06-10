"""Small JSON helpers."""

from __future__ import annotations

import json
from typing import Any


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def loads(text: str | bytes | None, default: Any = None) -> Any:
    if text is None:
        return default
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    try:
        return json.loads(text)
    except Exception:
        return default
