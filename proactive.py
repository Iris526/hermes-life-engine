"""Proactive intent, outbox, and delivery policy layer for LifeEngine v0.8.

This module deliberately separates three things:

1. ProactiveIntent: the Agent has something it wants to say.
2. ProactiveOutbox: a concrete message draft prepared for a target user.
3. ProactiveState: relationship-level cooldown, daily budget, and pending topics.

No external push delivery is performed here. Hermes adapters may later consume
queued outbox rows and mark them sent. This keeps the core framework-neutral and
traceable.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from . import life_author
from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id
from .time_utils import parse_datetime, to_epoch

ACTIVE_INTENT_STATUSES = {"generated", "queued"}
TERMINAL_INTENT_STATUSES = {"sent", "suppressed", "expired", "cancelled", "merged"}
PROACTIVE_MODES = {"off", "pending_only", "manual_send", "auto_send"}
_OUTBOX_AUTHOR_KIND = "proactive_outbox"
# 主动消息写作的旧机械开场：用于清理历史模板和模型偶发复述，作用域仅限
# proactive outbox 文案落库前的轻量保护，不参与策略判定。
_MECHANICAL_OUTBOX_PREFIXES = (
    "我有件事想跟你说",
    "我有一件事想跟你说",
    "有件事想跟你说",
)
# LifeAuthor 为 outbox 生成最终消息时使用的结构化输出合同。调用方只读取
# message_text 写入 proactive_outbox.draft_text，emotional_tone 仅供审计和
# 后续扩展，不改变当前发送状态机。
_OUTBOX_MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "message_text": {"type": "string"},
        "emotional_tone": {"type": "string"},
    },
    "required": ["message_text"],
}

# Default time-to-live (hours) per intent kind — so a proactive thought goes
# stale on its own instead of waiting forever to be said. A dream loses its
# point within a day; a request for help is more patient. The agent (the host
# is the agent itself) decides whether to actually say a still-fresh one; the
# engine just stops offering ones that have gone stale. An explicit expires_at
# always wins. _STALE_MAX_HOURS is a backstop that retires anything (even
# TTL-less legacy intents) left undelivered too long.
_DEFAULT_TTL_HOURS: dict[str, float] = {
    "dream_share": 24, "wake_share": 24, "self_reflection_share": 24, "dream": 24,
    "share_interesting": 36, "share": 36, "suggestion": 36,
    "report_progress": 48, "report_failure": 48,
    "ask_for_help": 72,
    # v0.18.0 P2 companion: idle outreach goes stale fast — a "想你" or a
    # follow-up question shouldn't surface a day later.
    "idle_share": 10, "ask_about_user": 18,
}
_DEFAULT_TTL_FALLBACK_HOURS = 36.0
_STALE_MAX_HOURS = 168.0  # 7 days: hard backstop for undelivered intents


def _default_expiry_iso(intent_type: str | None) -> str:
    from datetime import timedelta
    hours = _DEFAULT_TTL_HOURS.get(str(intent_type or ""), _DEFAULT_TTL_FALLBACK_HOURS)
    return (_now() + timedelta(hours=hours)).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _date_key() -> str:
    return _now().date().isoformat()


def _as_dict(row) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    if "delivery_policy_json" in d:
        d["delivery_policy"] = loads(d.pop("delivery_policy_json"), {})
    if "score_json" in d:
        d["score"] = loads(d.pop("score_json"), {})
    if "decision_json" in d:
        d["decision"] = loads(d.pop("decision_json"), {})
    if "payload_json" in d:
        d["payload"] = loads(d.pop("payload_json"), {})
    if "result_json" in d:
        d["result"] = loads(d.pop("result_json"), {})
    if "policy_json" in d:
        d["policy"] = loads(d.pop("policy_json"), {})
    if "pending_intent_ids_json" in d:
        d["pending_intent_ids"] = loads(d.pop("pending_intent_ids_json"), [])
    return d


def _get_canon_policy(conn, agent_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    if not row:
        return {}
    data = loads(row["data_json"], {}) or {}
    return data.get("proactive") or {}


def _gate_policy(control: dict[str, Any] | None, canon_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    canon_policy = canon_policy or {}
    gates = (control or {}).get("module_gates") or {}
    mode = str(gates.get("proactive") or canon_policy.get("mode") or "pending_only")
    if mode not in PROACTIVE_MODES:
        mode = "pending_only"
    return {
        "mode": mode,
        "max_per_day": int(canon_policy.get("max_per_day", canon_policy.get("daily_limit", 1))),
        "min_score_to_queue": int(canon_policy.get("min_score_to_queue", 50)),
        "min_score_to_auto_send": int(canon_policy.get("min_score_to_auto_send", 75)),
        "cooldown_minutes": int(canon_policy.get("cooldown_minutes", 180)),
        "quiet_hours": canon_policy.get("quiet_hours") or {},
        "timezone": canon_policy.get("timezone") or (canon_policy.get("quiet_hours") or {}).get("timezone") or "Asia/Shanghai",
        "default_target_user_id": canon_policy.get("default_target_user_id") or "anonymous-user",
    }


def ensure_proactive_state(conn, agent_id: str, user_id: str | None = None) -> dict[str, Any]:
    user_id = user_id or "anonymous-user"
    row = conn.execute(
        "SELECT * FROM agent_user_proactive_state WHERE agent_id=? AND user_id=?",
        (agent_id, user_id),
    ).fetchone()
    today = _date_key()
    if row:
        d = _as_dict(row) or {}
        if d.get("last_daily_reset_date") != today:
            conn.execute(
                "UPDATE agent_user_proactive_state SET daily_sent_count=0, last_daily_reset_date=?, updated_at=datetime('now') WHERE agent_id=? AND user_id=?",
                (today, agent_id, user_id),
            )
            row = conn.execute("SELECT * FROM agent_user_proactive_state WHERE agent_id=? AND user_id=?", (agent_id, user_id)).fetchone()
            d = _as_dict(row) or {}
        return d
    state_id = new_id("prostate")
    conn.execute(
        """INSERT INTO agent_user_proactive_state(id, agent_id, user_id, state, pending_intent_ids_json,
              user_responsiveness_score, interruption_sensitivity, daily_sent_count, last_daily_reset_date)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (state_id, agent_id, user_id, "silent", dumps([]), 50, 50, 0, today),
    )
    return _as_dict(conn.execute("SELECT * FROM agent_user_proactive_state WHERE id=?", (state_id,)).fetchone()) or {}


def list_proactive_states(conn, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_user_proactive_state WHERE agent_id=? ORDER BY updated_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def _target_user(intent: dict[str, Any], policy: dict[str, Any] | None = None) -> str:
    if intent.get("target_type") == "user" and intent.get("target_id"):
        return str(intent["target_id"])
    if intent.get("target_user_id"):
        return str(intent["target_user_id"])
    return str((policy or {}).get("default_target_user_id") or "anonymous-user")


def _update_state_pending(conn, agent_id: str, user_id: str, intent_id: str, state: str) -> dict[str, Any]:
    current = ensure_proactive_state(conn, agent_id, user_id)
    pending = list(current.get("pending_intent_ids") or [])
    active_states = {"has_something_to_share", "wants_help", "waiting_for_user_reply", "cooldown"}
    if intent_id not in pending and state in active_states:
        pending.append(intent_id)
    if state not in active_states:
        pending = [pid for pid in pending if pid != intent_id]
    conn.execute(
        """UPDATE agent_user_proactive_state SET state=?, pending_intent_ids_json=?, updated_at=datetime('now')
              WHERE agent_id=? AND user_id=?""",
        (state, dumps(pending), agent_id, user_id),
    )
    return ensure_proactive_state(conn, agent_id, user_id)


def _remove_pending_intents(conn, agent_id: str, intent_ids: list[str] | set[str]) -> int:
    """Remove retired intent ids from all per-user proactive state rows.

    Expiry/suppression can happen outside the original target user's state row,
    especially for legacy data. This keeps status/review surfaces from showing a
    stale "has something to share" state after the underlying intent is terminal.
    """
    retired = {str(i) for i in intent_ids if i}
    if not retired:
        return 0
    changed = 0
    rows = conn.execute(
        "SELECT agent_id, user_id, pending_intent_ids_json FROM agent_user_proactive_state WHERE agent_id=?",
        (agent_id,),
    ).fetchall()
    for row in rows:
        pending = loads(row["pending_intent_ids_json"], []) or []
        kept = [pid for pid in pending if str(pid) not in retired]
        if kept == pending:
            continue
        next_state = "silent" if not kept else "has_something_to_share"
        conn.execute(
            """UPDATE agent_user_proactive_state
                  SET state=?, pending_intent_ids_json=?, updated_at=datetime('now')
                WHERE agent_id=? AND user_id=?""",
            (next_state, dumps(kept), row["agent_id"], row["user_id"]),
        )
        changed += 1
    return changed


def get_proactive_intent(conn, intent_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proactive_intents WHERE id=?", (intent_id,)).fetchone()
    if not row:
        raise ValueError(f"proactive intent not found: {intent_id}")
    return _as_dict(row) or {}


def list_proactive_intents(conn, agent_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM proactive_intents WHERE agent_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proactive_intents WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, int(limit)),
        ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def create_proactive_intent(conn, agent_id: str, **payload: Any) -> dict[str, Any]:
    intent_id = new_id("proactive")
    # Default a per-type TTL so the thought expires on its own if never said.
    expires_at = payload.get("expires_at") or _default_expiry_iso(payload.get("intent_type"))
    expires_at_ts = None
    if expires_at:
        expires_at_ts = to_epoch(expires_at)
    conn.execute(
        """INSERT INTO proactive_intents(id, agent_id, target_type, target_id, trigger_event_id,
               trigger_result_id, intent_type, summary, emotional_tone, importance, urgency, novelty,
               relationship_relevance, privacy_level, status, delivery_policy_json, expires_at,
               expires_at_ts, generated_by, trace_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            intent_id,
            agent_id,
            payload.get("target_type", "self_journal"),
            payload.get("target_id"),
            payload.get("trigger_event_id"),
            payload.get("trigger_result_id"),
            payload.get("intent_type", "share_interesting"),
            payload.get("summary", ""),
            payload.get("emotional_tone"),
            int(payload.get("importance", 50)),
            int(payload.get("urgency", 50)),
            int(payload.get("novelty", 50)),
            int(payload.get("relationship_relevance", 50)),
            payload.get("privacy_level", "safe_to_share"),
            payload.get("status", "generated"),
            dumps(payload.get("delivery_policy", {})),
            expires_at,
            expires_at_ts,
            payload.get("generated_by", payload.get("source", "life_commit")),
            payload.get("trace_id"),
        ),
    )
    append_journal(conn, "agent", agent_id, "proactive_intent_created", {"intent_id": intent_id, "summary": payload.get("summary", "")}, payload.get("source") or "proactive")
    return get_proactive_intent(conn, intent_id)


def _score_intent(intent: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    importance = int(intent.get("importance") or 50)
    urgency = int(intent.get("urgency") or 50)
    novelty = int(intent.get("novelty") or 50)
    relevance = int(intent.get("relationship_relevance") or 50)
    interruption = int((state or {}).get("interruption_sensitivity") or 50)
    daily_count = int((state or {}).get("daily_sent_count") or 0)
    raw = importance * 0.35 + urgency * 0.25 + novelty * 0.15 + relevance * 0.25
    penalty = max(0, interruption - 50) * 0.25 + daily_count * 12
    score = max(0, min(100, round(raw - penalty, 2)))
    return {"score": score, "importance": importance, "urgency": urgency, "novelty": novelty, "relationship_relevance": relevance, "interruption_penalty": penalty}


def _is_expired(intent: dict[str, Any]) -> bool:
    ts = intent.get("expires_at_ts")
    if ts is None and intent.get("expires_at"):
        try:
            ts = to_epoch(intent.get("expires_at"))
        except Exception:
            ts = None
    return ts is not None and int(ts) <= int(_now().timestamp())


def _within_cooldown(state: dict[str, Any] | None) -> bool:
    """True if we're still inside the post-send cooldown window.

    The window is stored as ``next_allowed_proactive_at`` when a message is sent
    (see ``mark_outbox_sent``). Until this fix it was written but never read, so
    only the per-day cap throttled bursts; now the time-based cooldown is real.
    """
    nxt = (state or {}).get("next_allowed_proactive_at")
    if not nxt:
        return False
    try:
        return to_epoch(nxt) > int(_now().timestamp())
    except Exception:
        return False


def _quiet_hours_active(policy: dict[str, Any]) -> bool:
    return bool(quiet_hours_status(policy).get("active"))


def _parse_hhmm(value: Any) -> tuple[int, int] | None:
    try:
        hh, mm = str(value).split(":", 1)
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        return None
    return None


def _sqlite_utc_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def quiet_hours_status(policy: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return quiet-hours state and the next safe delivery time.

    ``send_after`` is compared against SQLite ``datetime('now')`` elsewhere, so
    ``next_allowed_at`` deliberately uses SQLite's UTC text format rather than
    ISO strings with offsets.
    """
    qh = policy.get("quiet_hours") or {}
    if not isinstance(qh, dict) or not qh.get("start") or not qh.get("end"):
        return {"active": False, "timezone": str(policy.get("timezone") or "Asia/Shanghai"), "next_allowed_at": None}
    # Use the user's/agent's local clock for quiet hours. Earlier versions used
    # UTC here, so QQ bedtime (e.g. 02:46 Asia/Shanghai) could be misread as
    # daytime and an idle push would slip through right after “晚安”.
    tz_name = str(policy.get("timezone") or qh.get("timezone") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
        tz_name = "UTC"
    start = _parse_hhmm(qh.get("start"))
    end = _parse_hhmm(qh.get("end"))
    if not start or not end or start == end:
        return {"active": False, "timezone": tz_name, "next_allowed_at": None}
    local_now = (now or _now()).astimezone(tz)
    start_dt = local_now.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    end_dt = local_now.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    if start < end:
        active = start_dt <= local_now < end_dt
        next_allowed = end_dt if active else None
    elif local_now >= start_dt:
        active = True
        next_allowed = end_dt + timedelta(days=1)
    elif local_now < end_dt:
        active = True
        next_allowed = end_dt
    else:
        active = False
        next_allowed = None
    return {
        "active": active,
        "timezone": tz_name,
        "start": qh.get("start"),
        "end": qh.get("end"),
        "next_allowed_at": _sqlite_utc_datetime(next_allowed) if next_allowed else None,
    }


def list_outbox(conn, agent_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM proactive_outbox WHERE agent_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proactive_outbox WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, int(limit)),
        ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def get_outbox_message(conn, outbox_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proactive_outbox WHERE id=?", (outbox_id,)).fetchone()
    if not row:
        raise ValueError(f"proactive outbox not found: {outbox_id}")
    return _as_dict(row) or {}


def _create_outbox(conn, agent_id: str, user_id: str, intent: dict[str, Any], draft_text: str, status: str = "queued", delivery_channel: str | None = None) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT * FROM proactive_outbox WHERE agent_id=? AND intent_id=? AND status IN ('drafted','queued') ORDER BY created_at DESC LIMIT 1",
        (agent_id, intent["id"]),
    ).fetchone()
    if existing:
        return _as_dict(existing) or {}
    outbox_id = new_id("outbox")
    conn.execute(
        """INSERT INTO proactive_outbox(id, agent_id, target_user_id, intent_id, draft_text, status, delivery_channel)
              VALUES(?,?,?,?,?,?,?)""",
        (outbox_id, agent_id, user_id, intent["id"], draft_text, status, delivery_channel),
    )
    return get_outbox_message(conn, outbox_id)


def _trim_message_text(text: str) -> str:
    """清理主动消息文本，供 outbox 写入前做最后一道轻量保护。

    输入来自 LifeAuthor 返回值或确定性兜底文案；返回值是可直接持久化
    到 proactive_outbox.draft_text 的短消息。它不访问外部服务，只做空白、
    引号和旧模板前缀的规整；如果最终为空，调用方继续走兜底。
    """
    msg = str(text or "").strip().strip("\"'“”")
    for prefix in _MECHANICAL_OUTBOX_PREFIXES:
        if msg.startswith(prefix):
            msg = msg[len(prefix):].lstrip("：:，,。 ")
            break
    return msg.strip()


def _fallback_outbox_text(intent: dict[str, Any]) -> str:
    """生成无模型时的主动消息兜底文案。

    作用域限定在 proactive evaluate 的 outbox 创建流程；调用方是
    ``evaluate_proactive_intent``。它不能理解具体角色，只负责不再暴露
    “我有件事想跟你说/资源不足/重新规划”这类系统播报腔；失败或空摘要
    时返回一条短而保守的中文消息。
    """
    summary = _trim_message_text(str(intent.get("summary") or ""))
    if not summary:
        return "这边有点卡住，我想先缓一缓，换个更稳的做法。"
    summary = summary.replace("遇到资源不足，想重新规划", "这边手头有点不够，我想先缓一下，重新盘算个稳一点的做法")
    summary = summary.replace("遇到资源不足", "这边手头有点不够")
    summary = summary.replace("资源不足", "手头有点不够")
    summary = summary.replace("重新规划", "重新盘算一下")
    if not summary.endswith(("。", "！", "？", "…", ".", "!", "?")):
        summary += "。"
    return summary


def _author_outbox_text(conn, agent_id: str, user_id: str, intent: dict[str, Any], *,
                        trace_id: str | None = None) -> str | None:
    """把主动意图改写成符合角色语气的可发送消息。

    输入是已通过策略、隐私和节奏检查的 proactive intent；输出是一条将
    写入 proactive_outbox 的自然语言消息，或在 host 模型不可用、门控关闭、
    预算耗尽、模型返回无效时返回 ``None``。调用方式是同步 best-effort：
    它只请求 LifeAuthor 生成内容，不修改资源账本、不提交事务、不绕过现有
    发送策略；失败由调用方使用确定性兜底，用户可见影响是语气退回保守模板。
    """
    context = {
        "主动意图": {
            "类型": intent.get("intent_type"),
            "原始摘要": intent.get("summary"),
            "情绪倾向": intent.get("emotional_tone"),
            "重要性": intent.get("importance"),
            "紧急度": intent.get("urgency"),
            "新鲜度": intent.get("novelty"),
            "关系相关度": intent.get("relationship_relevance"),
            "生成来源": intent.get("generated_by"),
        },
        "收件人": {"target_user_id": user_id},
        "写作目标": "把主动意图写成一条你会直接发给对方的聊天消息。",
        "不要出现": [
            "我有件事想跟你说",
            "我有一件事想跟你说",
            "LifeEngine",
            "outbox",
            "调度",
            "资源不足",
            "重新规划",
        ],
    }
    instructions = (
        "你准备主动给对方发一条消息。请把上下文里的主动意图改写成你本人会发出的"
        "一句短消息：自然、有性格、有一点当下的情绪，但不要表演腔，也不要像系统播报。"
        "如果原始摘要里有工程或资源账本味道的词，只转成生活里的说法，不要照抄。"
        "不要使用“我有件事想跟你说”这类开场。"
        "输出 message_text=最终可发送消息；emotional_tone=语气标签。"
    )
    parsed = life_author.author(
        conn,
        "agent",
        agent_id,
        kind=_OUTBOX_AUTHOR_KIND,
        instructions=instructions,
        context=context,
        schema=_OUTBOX_MESSAGE_SCHEMA,
        max_tokens=220,
        temperature=0.75,
        trace_id=trace_id,
    )
    if not parsed:
        return None
    msg = _trim_message_text(str(parsed.get("message_text") or ""))
    if not msg or "资源不足" in msg or "LifeEngine" in msg or "outbox" in msg:
        return None
    return msg


def author_outbox_text(conn, agent_id: str, user_id: str, intent: dict[str, Any], *,
                       trace_id: str | None = None) -> str | None:
    """在 LifeOps 写事务外生成 proactive outbox 最终文案。

    输入是已经存在的 proactive intent 和目标 user id；输出是一条可直接写入
    proactive_outbox 的聊天消息，或在宿主模型不可用/门控关闭/返回无效时为 `None`。
    调用方是 runtime 的 proactive evaluate 预处理和测试；副作用仅限 LifeAuthor
    调用审计，不改变 intent/outbox 状态。事务内 evaluate 会优先使用该 draft_text，
    并在缺失时回退确定性模板。
    """
    return _author_outbox_text(conn, agent_id, user_id, intent, trace_id=trace_id)


def evaluate_proactive_intent(
    conn,
    agent_id: str,
    intent_id: str | None = None,
    *,
    control: dict[str, Any] | None = None,
    target_user_id: str | None = None,
    manual: bool = False,
    trace_id: str | None = None,
    draft_text: str | None = None,
    allow_authoring: bool = True,
) -> dict[str, Any]:
    """评估 proactive intent 并按策略排队或生成 outbox。

    输入来自 LifeOps `EVALUATE_PROACTIVE_INTENT`、工具或 heartbeat；`draft_text`
    是事务外预生成的最终消息，`allow_authoring=False` 表示本函数不得在写事务内调用
    LifeAuthor。输出是逐 intent 的评估决策和可选 outbox。副作用是更新 intent/state、
    写 proactive_evaluations/journal/outbox；失败由外层事务回滚。该函数保留旧的
    `allow_authoring=True` 兼容直接模块调用，但 runtime 的 LifeOps 路径会传 false。
    """
    canon_policy = _get_canon_policy(conn, agent_id)
    policy = _gate_policy(control, canon_policy)
    mode = policy["mode"]
    if intent_id:
        intents = [get_proactive_intent(conn, intent_id)]
    else:
        intents = list_proactive_intents(conn, agent_id, status="generated", limit=10)
    evaluated: list[dict[str, Any]] = []
    for intent in intents:
        user_id = target_user_id or _target_user(intent, policy)
        state = ensure_proactive_state(conn, agent_id, user_id)
        score = _score_intent(intent, state)
        decision = "none"
        reason = ""
        outbox = None
        if intent.get("status") in TERMINAL_INTENT_STATUSES:
            decision, reason = "skip", f"terminal status {intent.get('status')}"
        elif _is_expired(intent):
            conn.execute("UPDATE proactive_intents SET status='expired', expired_at=datetime('now'), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "expire", "reason": "expires_at passed"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "silent")
            decision, reason = "expire", "expires_at passed"
        elif mode == "off":
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("proactive module off", dumps(score), dumps({"decision": "suppress", "reason": "proactive module off"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "proactive module off"
        elif intent.get("privacy_level") == "agent_private" and intent.get("target_type") == "user":
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("agent_private cannot target user", dumps(score), dumps({"decision": "suppress", "reason": "agent_private cannot target user"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "agent_private cannot target user"
        elif score["score"] < policy["min_score_to_queue"] and not manual:
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("score below queue threshold", dumps(score), dumps({"decision": "suppress", "reason": "score below queue threshold", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "score below queue threshold"
        elif mode == "pending_only":
            conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "pending_only", "reason": "keep pending for next turn", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
            decision, reason = "queue_pending", "pending_only keeps it for next user turn"
        elif mode == "manual_send" and not manual:
            conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "manual_send_pending", "reason": "manual approval required", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
            decision, reason = "queue_pending", "manual_send requires explicit send"
        else:
            if _quiet_hours_active(policy) and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "quiet_hours", "reason": "quiet hours active", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
                decision, reason = "queue_pending", "quiet hours active"
            elif int(state.get("daily_sent_count") or 0) >= int(policy["max_per_day"]) and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "daily_limit", "reason": "daily proactive budget exhausted", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "cooldown")
                decision, reason = "queue_pending", "daily proactive budget exhausted"
            elif not manual and _within_cooldown(state):
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "cooldown", "reason": "within proactive cooldown window", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "cooldown")
                decision, reason = "queue_pending", "within proactive cooldown window"
            elif mode == "auto_send" and score["score"] < policy["min_score_to_auto_send"] and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "score_below_auto_send", "reason": "queued but not pushed", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
                decision, reason = "queue_pending", "score below auto-send threshold"
            else:
                msg = (
                    draft_text
                    or (_author_outbox_text(conn, agent_id, user_id, intent, trace_id=trace_id) if allow_authoring else None)
                    or _fallback_outbox_text(intent)
                )
                outbox = _create_outbox(conn, agent_id, user_id, intent, msg, status="queued", delivery_channel="hermes")
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), result_outbox_id=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (outbox.get("id"), dumps(score), dumps({"decision": "outbox_queued", "reason": "delivery allowed", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "waiting_for_user_reply")
                decision, reason = "outbox_queued", "delivery allowed"
        eval_id = new_id("proeval")
        conn.execute(
            """INSERT INTO proactive_evaluations(id, agent_id, target_user_id, intent_id, mode, score,
                  decision, reason, policy_json, trace_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (eval_id, agent_id, user_id, intent["id"], mode, float(score["score"]), decision, reason, dumps(policy), trace_id),
        )
        append_journal(conn, "agent", agent_id, "proactive_intent_evaluated", {"intent_id": intent["id"], "decision": decision, "reason": reason, "score": score, "outbox_id": outbox.get("id") if outbox else None}, "proactive")
        evaluated.append({"evaluation_id": eval_id, "intent_id": intent["id"], "decision": decision, "reason": reason, "score": score, "outbox": outbox, "state": ensure_proactive_state(conn, agent_id, user_id)})
    return {"evaluated": evaluated, "policy": policy}


def mark_outbox_sent(conn, agent_id: str, outbox_id: str, *, result: dict[str, Any] | None = None, manual: bool = True) -> dict[str, Any]:
    msg = get_outbox_message(conn, outbox_id)
    if msg.get("agent_id") != agent_id:
        raise ValueError("outbox owner mismatch")
    user_id = msg.get("target_user_id") or "anonymous-user"
    intent_id = msg.get("intent_id")
    conn.execute(
        "UPDATE proactive_outbox SET status='sent', sent_at=datetime('now'), delivery_result_json=? WHERE id=?",
        (dumps(result or {"manual": manual}), outbox_id),
    )
    if intent_id:
        conn.execute("UPDATE proactive_intents SET status='sent', sent_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (intent_id,))
    state = ensure_proactive_state(conn, agent_id, user_id)
    pending = [pid for pid in (state.get("pending_intent_ids") or []) if pid != intent_id]
    cooldown_minutes = int(_get_canon_policy(conn, agent_id).get("cooldown_minutes", 180))
    cooldown = (_now() + timedelta(minutes=cooldown_minutes)).isoformat()
    conn.execute(
        """UPDATE agent_user_proactive_state SET state='cooldown', pending_intent_ids_json=?, last_proactive_sent_at=datetime('now'),
              next_allowed_proactive_at=?, daily_sent_count=daily_sent_count+1, updated_at=datetime('now')
              WHERE agent_id=? AND user_id=?""",
        (dumps(pending), cooldown, agent_id, user_id),
    )
    append_journal(conn, "agent", agent_id, "proactive_outbox_sent", {"outbox_id": outbox_id, "intent_id": intent_id}, "proactive")
    return {"outbox": get_outbox_message(conn, outbox_id), "state": ensure_proactive_state(conn, agent_id, user_id)}


def suppress_intent(conn, agent_id: str, intent_id: str, reason: str = "manual suppress") -> dict[str, Any]:
    intent = get_proactive_intent(conn, intent_id)
    user_id = _target_user(intent, {"default_target_user_id": "anonymous-user"})
    conn.execute(
        "UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, updated_at=datetime('now') WHERE id=?",
        (reason, intent_id),
    )
    conn.execute(
        """UPDATE proactive_outbox
              SET status='suppressed', suppression_reason=?, error=NULL
            WHERE intent_id=? AND status IN ('drafted','queued')""",
        (reason, intent_id),
    )
    _update_state_pending(conn, agent_id, user_id, intent_id, "suppressed_by_policy")
    append_journal(conn, "agent", agent_id, "proactive_intent_suppressed", {"intent_id": intent_id, "reason": reason}, "proactive")
    return get_proactive_intent(conn, intent_id)


def expire_intents(conn, agent_id: str) -> dict[str, Any]:
    # Expire anything past its TTL, plus a by-age backstop: any undelivered
    # intent older than _STALE_MAX_HOURS is retired even if it never got a TTL
    # (cleans legacy/TTL-less piles so the agent is only ever offered fresh ones).
    now_ts = int(_now().timestamp())
    rows = conn.execute(
        """SELECT id FROM proactive_intents
              WHERE agent_id=? AND status IN ('generated','queued')
                AND ( (expires_at_ts IS NOT NULL AND expires_at_ts <= ?)
                      OR created_at <= datetime('now', ?) )""",
        (agent_id, now_ts, f"-{_STALE_MAX_HOURS:g} hours"),
    ).fetchall()
    expired = []
    for r in rows:
        conn.execute("UPDATE proactive_intents SET status='expired', expired_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (r["id"],))
        expired.append(r["id"])
    if expired:
        conn.executemany(
            """UPDATE proactive_outbox
                  SET status='expired', suppression_reason='intent expired', error=NULL
                WHERE intent_id=? AND status IN ('drafted','queued')""",
            [(intent_id,) for intent_id in expired],
        )
        state_rows_changed = _remove_pending_intents(conn, agent_id, expired)
        append_journal(conn, "agent", agent_id, "proactive_intents_expired", {"intent_ids": expired}, "proactive")
    else:
        state_rows_changed = 0
    return {"expired": expired, "count": len(expired), "state_rows_changed": state_rows_changed}
