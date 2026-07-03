"""Deterministic world-evolution planning for the heartbeat.

本模块只负责读取当前世界/社会运行态并生成 LifeOps；真正写库必须由
runtime 的 LifeOps 提交流程完成。这样条件过期、流言降温和声望回归都能保留
validation、savepoint、receipt 与 journal。
"""

from __future__ import annotations

from typing import Any

from .jsonutil import loads
from .time_utils import to_epoch

RUMOR_DECAY_PER_HOUR = 0.02
RUMOR_FADE_THRESHOLD = 0.08

REPUTATION_NEUTRAL_BASELINE = 0.0
REPUTATION_REGRESSION_IDLE_DAYS = 7
REPUTATION_REGRESSION_STEP_PER_WINDOW = 2.0
REPUTATION_REGRESSION_MAX_STEP = 10.0

_MIN_EFFECTIVE_CHANGE = 1e-6


def _epoch(value: str | None) -> int | None:
    """把数据库时间字段转成 epoch 秒。

    输入来自 world/social 表的 ISO 字符串或 SQLite `datetime('now')` 文本；
    输出用于和 heartbeat 的逻辑 `now` 比较。解析失败时返回 None，让规划层跳过
    单条坏数据而不是让整个 heartbeat 失败。
    """
    try:
        return to_epoch(value)
    except Exception:
        return None


def _json_obj(value: Any) -> dict[str, Any]:
    """读取 JSON 对象字段。

    输入来自 `*_json` 数据库列；输出始终是 dict，调用方会把它原样带回 LifeOps
    payload，避免过期状态更新时擦掉原有结构化证据或扩展 payload。
    """
    parsed = loads(value, {}) if isinstance(value, str) else (value or {})
    return parsed if isinstance(parsed, dict) else {}


def _clamp(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    """把可编辑数值压到公式允许范围内。"""
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _condition_expiry_items(conn, owner_kind: str, owner_id: str, *, now: str, now_ts: int) -> list[dict[str, Any]]:
    """规划过期 world_conditions 的 LifeOps。

    输入是 heartbeat owner 与逻辑 now；输出是一组 WORLD_UPSERT_CONDITION op 项。
    只处理非终态且 `ends_at <= now` 的行，已 expired/resolved/archived 的行天然
    幂等跳过。副作用为零，真正写入由 runtime 后续提交 LifeOps 完成。
    """
    rows = conn.execute(
        """SELECT * FROM world_conditions
             WHERE owner_kind=? AND owner_id=? AND ends_at IS NOT NULL
               AND status NOT IN ('resolved','expired','archived')
             ORDER BY ends_at ASC, updated_at ASC
             LIMIT 200""",
        (owner_kind, owner_id),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        end_ts = _epoch(row["ends_at"])
        if end_ts is None or end_ts > now_ts:
            continue
        evidence = _json_obj(row["evidence_json"])
        evidence["world_evolution"] = {
            "action": "condition_expired",
            "expired_at": now,
            "previous_status": row["status"],
            "ends_at": row["ends_at"],
        }
        payload = {
            "key": row["key"],
            "title": row["title"],
            "condition_type": row["condition_type"],
            "scope_kind": row["scope_kind"],
            "scope_id": row["scope_id"],
            "severity": row["severity"],
            "intensity": row["intensity"],
            "summary": row["summary"],
            "content": row["content"],
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "payload": _json_obj(row["payload_json"]),
            "evidence": evidence,
            "status": "expired",
            "source": "heartbeat_world_evolution",
        }
        items.append({
            "category": "condition_expired",
            "target_id": row["id"],
            "op": {"type": "WORLD_UPSERT_CONDITION", "payload": payload},
        })
    return items


def _rumor_decay_items(conn, owner_kind: str, owner_id: str, *, now: str, now_ts: int) -> list[dict[str, Any]]:
    """规划 active rumors 的热度衰减。

    衰减公式是 `heat -= elapsed_hours * RUMOR_DECAY_PER_HOUR`，下限为 0；
    当新热度小于等于 `RUMOR_FADE_THRESHOLD` 时转为 faded 且 heat 置 0。水位是
    rumor.updated_at，因此同一逻辑 now 重跑 elapsed 为 0，不会重复衰减。
    """
    rows = conn.execute(
        """SELECT * FROM rumors
             WHERE owner_kind=? AND owner_id=? AND status='active'
             ORDER BY updated_at ASC
             LIMIT 200""",
        (owner_kind, owner_id),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        updated_ts = _epoch(row["updated_at"])
        if updated_ts is None or updated_ts >= now_ts:
            continue
        elapsed_hours = max(0.0, (now_ts - updated_ts) / 3600.0)
        old_heat = _clamp(row["heat"], 0.0, 1.0, 0.0)
        decay = min(old_heat, elapsed_hours * RUMOR_DECAY_PER_HOUR)
        if decay <= _MIN_EFFECTIVE_CHANGE:
            continue
        next_heat = max(0.0, old_heat - decay)
        next_status = "active"
        if next_heat <= RUMOR_FADE_THRESHOLD:
            next_heat = 0.0
            next_status = "faded"
        payload = {
            "rumor_id": row["id"],
            "heat": round(next_heat, 6),
            "status": next_status,
            "effective_at": now,
            "previous_heat": round(old_heat, 6),
            "elapsed_hours": round(elapsed_hours, 6),
            "reason": "time decay",
            "source": "heartbeat_world_evolution",
        }
        items.append({
            "category": "rumor_decayed" if next_status == "active" else "rumor_faded",
            "target_id": row["id"],
            "op": {"type": "SOCIAL_RUMOR_DECAY", "payload": payload},
        })
    return items


def _reputation_regression_items(conn, owner_kind: str, owner_id: str, *, now: str, now_ts: int) -> list[dict[str, Any]]:
    """规划长时间无事件的 reputation_accounts 回归。

    每个 account 以最新 reputation_event.created_at 为水位；若没有事件则回退到
    account.updated_at。每满 `REPUTATION_REGRESSION_IDLE_DAYS` 天回归
    `REPUTATION_REGRESSION_STEP_PER_WINDOW` 点，并限制单 tick 最大回归步长，且
    永远不跨过中性基线。输出是 SOCIAL_REPUTATION_EVENT op，由既有声望账本写入。
    """
    rows = conn.execute(
        """SELECT a.*,
                  (SELECT e.created_at
                     FROM reputation_events e
                    WHERE e.owner_kind=a.owner_kind
                      AND e.owner_id=a.owner_id
                      AND e.subject_entity_id=a.subject_entity_id
                      AND e.audience_entity_id=a.audience_entity_id
                      AND e.axis=a.axis
                    ORDER BY COALESCE(unixepoch(e.created_at), 0) DESC, e.created_at DESC
                    LIMIT 1) AS last_event_at
             FROM reputation_accounts a
            WHERE a.owner_kind=? AND a.owner_id=? AND a.status='active'
            ORDER BY ABS(a.value) DESC, a.updated_at ASC
            LIMIT 200""",
        (owner_kind, owner_id),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        value = _clamp(row["value"], -100.0, 100.0, 0.0)
        distance = value - REPUTATION_NEUTRAL_BASELINE
        if abs(distance) <= _MIN_EFFECTIVE_CHANGE:
            continue
        last_at = row["last_event_at"] or row["updated_at"]
        last_ts = _epoch(last_at)
        if last_ts is None or last_ts >= now_ts:
            continue
        elapsed_days = max(0.0, (now_ts - last_ts) / 86400.0)
        windows = int(elapsed_days // REPUTATION_REGRESSION_IDLE_DAYS)
        if windows <= 0:
            continue
        magnitude = min(
            abs(distance),
            REPUTATION_REGRESSION_MAX_STEP,
            windows * REPUTATION_REGRESSION_STEP_PER_WINDOW,
        )
        if magnitude <= _MIN_EFFECTIVE_CHANGE:
            continue
        delta = -magnitude if distance > 0 else magnitude
        payload = {
            "subject_entity_id": row["subject_entity_id"],
            "audience_entity_id": row["audience_entity_id"],
            "axis": row["axis"],
            "delta": round(delta, 6),
            "reason": "sedentary regression",
            "evidence_kind": "world_evolution",
            "evidence_id": row["id"],
            "evidence": {
                "account_id": row["id"],
                "baseline": REPUTATION_NEUTRAL_BASELINE,
                "previous_value": round(value, 6),
                "idle_days": round(elapsed_days, 6),
                "elapsed_windows": windows,
                "step_per_window": REPUTATION_REGRESSION_STEP_PER_WINDOW,
                "last_event_at": last_at,
            },
            "effective_at": now,
            "source": "heartbeat_world_evolution",
        }
        items.append({
            "category": "reputation_regressed",
            "target_id": row["id"],
            "op": {"type": "SOCIAL_REPUTATION_EVENT", "payload": payload},
        })
    return items


def plan_world_evolution(conn, owner_kind: str, owner_id: str, *, now: str) -> dict[str, Any]:
    """生成本轮 world-evolution heartbeat 的 LifeOps 计划。

    调用方是 `LifeEngineRuntime._run_world_evolution_for_tick`。函数只读数据库并返回
    分类后的 op 项，不直接修改任何领域表；不可解析的 `now` 会返回 skipped，避免
    心跳因时间字符串坏掉而整体失败。
    """
    now_ts = _epoch(now)
    if now_ts is None:
        return {"status": "skipped", "reason": "unparseable now", "items": []}
    condition_items = _condition_expiry_items(conn, owner_kind, owner_id, now=now, now_ts=now_ts)
    rumor_items = _rumor_decay_items(conn, owner_kind, owner_id, now=now, now_ts=now_ts)
    reputation_items = _reputation_regression_items(conn, owner_kind, owner_id, now=now, now_ts=now_ts)
    items = condition_items + rumor_items + reputation_items
    return {
        "status": "planned",
        "items": items,
        "counts": {
            "condition_expirations": len(condition_items),
            "rumor_updates": len(rumor_items),
            "reputation_regressions": len(reputation_items),
        },
        "formulas": {
            "rumor_decay_per_hour": RUMOR_DECAY_PER_HOUR,
            "rumor_fade_threshold": RUMOR_FADE_THRESHOLD,
            "reputation_idle_days": REPUTATION_REGRESSION_IDLE_DAYS,
            "reputation_step_per_window": REPUTATION_REGRESSION_STEP_PER_WINDOW,
            "reputation_max_step": REPUTATION_REGRESSION_MAX_STEP,
            "reputation_baseline": REPUTATION_NEUTRAL_BASELINE,
        },
    }
