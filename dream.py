"""DreamRun / DreamAudit / DreamEntry operations for LifeEngine v0.11.3.

Dream is not a real-world fact layer.  Dream entries use ``truth_layer =
'dream_symbolic'`` and may generate shareable proactive intents, but they do
not mutate ordinary life state unless an explicit follow-up LifeOp is later
committed.  DreamAudit is the nightly self-check pass: it looks for missing
state-flow/resource/reply bookkeeping after sleep and records findings.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .memory import create_memory
from .proactive import create_proactive_intent
from .resources import reconcile_resources
from .time_utils import now_iso, to_epoch
from .trace import append_journal, new_id
from .sleep_reply_dream_policy import get_policy as get_srd_policy, render_dream_share

MIN_CORE_DREAM_MINUTES = 90


def _decode_row(row) -> dict[str, Any]:
    if not row:
        return {}
    d = dict(row)
    for key, default in [
        ("metadata_json", {}),
        ("audit_summary_json", {}),
        ("narrative_inputs_json", {}),
        ("proposed_ops_json", []),
        ("symbols_json", []),
        ("source_memory_ids_json", []),
        ("source_event_ids_json", []),
        ("source_goal_ids_json", []),
        ("source_finding_ids_json", []),
    ]:
        if key in d:
            d[key[:-5] if key.endswith("_json") else key] = loads(d.pop(key), default)
    return d


def _active_dream_policy(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (owner_kind, owner_id),
    ).fetchone()
    if not row:
        return {}
    data = loads(row["data_json"], {}) or {}
    return data.get("dream") or {}


def _latest_sleep_session_without_dream(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT s.* FROM sleep_sessions s
              LEFT JOIN dream_runs d ON d.sleep_session_id=s.id AND d.owner_kind=s.owner_kind AND d.owner_id=s.owner_id
             WHERE s.owner_kind=? AND s.owner_id=? AND s.status IN ('completed','interrupted')
               AND d.id IS NULL
             ORDER BY COALESCE(s.actual_wake_at_ts, unixepoch(s.completed_at), unixepoch(s.updated_at), unixepoch(s.created_at)) DESC
             LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    return dict(row) if row else None


def get_dream_run(conn, dream_run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM dream_runs WHERE id=?", (dream_run_id,)).fetchone()
    if not row:
        raise ValueError(f"dream run not found: {dream_run_id}")
    run = _decode_row(row)
    run["findings"] = list_dream_findings(conn, run["owner_kind"], run["owner_id"], dream_run_id=run["id"], limit=100)
    run["entries"] = list_dream_entries(conn, run["owner_kind"], run["owner_id"], dream_run_id=run["id"], limit=20)
    return run


def list_dream_runs(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if status:
        clause += " AND status=?"
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM dream_runs WHERE {clause} ORDER BY started_at DESC, created_at DESC LIMIT ?", tuple(params)).fetchall()
    return [_decode_row(r) for r in rows]


def list_dream_findings(conn, owner_kind: str, owner_id: str, dream_run_id: str | None = None, severity: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if dream_run_id:
        clause += " AND dream_run_id=?"
        params.append(dream_run_id)
    if severity:
        clause += " AND severity=?"
        params.append(severity)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM dream_audit_findings WHERE {clause} ORDER BY created_at DESC LIMIT ?", tuple(params)).fetchall()
    return [_decode_row(r) for r in rows]


def list_dream_entries(conn, owner_kind: str, owner_id: str, dream_run_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if dream_run_id:
        clause += " AND dream_run_id=?"
        params.append(dream_run_id)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM dream_entries WHERE {clause} ORDER BY created_at DESC LIMIT ?", tuple(params)).fetchall()
    return [_decode_row(r) for r in rows]


def get_dream_entry(conn, dream_entry_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM dream_entries WHERE id=?", (dream_entry_id,)).fetchone()
    if not row:
        raise ValueError(f"dream entry not found: {dream_entry_id}")
    return _decode_row(row)


def _insert_finding(conn, owner_kind: str, owner_id: str, dream_run_id: str, *, finding_type: str, severity: str,
                    message: str, target_kind: str | None = None, target_id: str | None = None,
                    proposed_ops: list[dict[str, Any]] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    fid = new_id("dreamfind")
    conn.execute(
        """INSERT INTO dream_audit_findings(
             id, owner_kind, owner_id, dream_run_id, finding_type, severity, target_kind, target_id,
             message, proposed_ops_json, metadata_json, status
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fid, owner_kind, owner_id, dream_run_id, finding_type, severity, target_kind, target_id,
         message, dumps(proposed_ops or []), dumps(metadata or {}), "open"),
    )
    return _decode_row(conn.execute("SELECT * FROM dream_audit_findings WHERE id=?", (fid,)).fetchone())


def run_dream_audit(conn, owner_kind: str, owner_id: str, dream_run_id: str, *, sleep_session: dict[str, Any] | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    wake_ts = None
    if sleep_session:
        wake_ts = sleep_session.get("actual_wake_at_ts")

    # 1. Resource materialized-account drift.
    try:
        rec = reconcile_resources(conn, owner_kind, owner_id, record=False)
        for mismatch in rec.get("mismatches", []):
            findings.append(_insert_finding(
                conn, owner_kind, owner_id, dream_run_id,
                finding_type="resource_ledger_drift", severity="error", target_kind="resource",
                target_id=mismatch.get("resource_key"),
                message=f"Resource {mismatch.get('resource_key')} account does not match ledger.",
                metadata=mismatch,
            ))
    except Exception as exc:
        findings.append(_insert_finding(conn, owner_kind, owner_id, dream_run_id, finding_type="resource_reconcile_error", severity="warning", message=f"Resource reconcile failed: {type(exc).__name__}: {exc}"))

    # 2. Active schedule blocks whose planned end has passed.  This catches
    # forgotten execution/settlement after a long sleep.
    try:
        if wake_ts:
            rows = conn.execute(
                """SELECT id,event_id,status,end_ts FROM schedule_blocks
                     WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
                       AND end_ts IS NOT NULL AND end_ts < ?
                     ORDER BY end_ts ASC LIMIT 20""",
                (owner_kind, owner_id, int(wake_ts)),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id,event_id,status,end_ts FROM schedule_blocks
                     WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
                       AND end_ts IS NOT NULL AND end_ts < unixepoch('now')
                     ORDER BY end_ts ASC LIMIT 20""",
                (owner_kind, owner_id),
            ).fetchall()
        for row in rows:
            if sleep_session and row["id"] == sleep_session.get("schedule_block_id"):
                continue
            findings.append(_insert_finding(
                conn, owner_kind, owner_id, dream_run_id,
                finding_type="stale_schedule_block", severity="warning", target_kind="schedule_block", target_id=row["id"],
                message=f"Schedule block {row['id']} ended but remains {row['status']}.",
                proposed_ops=[{"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": row["id"], "status": "missed", "reason": "DreamAudit found stale schedule block"}}],
                metadata=dict(row),
            ))
    except Exception as exc:
        findings.append(_insert_finding(conn, owner_kind, owner_id, dream_run_id, finding_type="schedule_audit_error", severity="warning", message=f"Schedule audit failed: {type(exc).__name__}: {exc}"))

    # 3. Wake jobs stuck in running and pending delayed replies.
    try:
        rows = conn.execute(
            "SELECT * FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='running' ORDER BY wake_at_ts ASC LIMIT 20",
            (owner_kind, owner_id),
        ).fetchall()
        for row in rows:
            findings.append(_insert_finding(conn, owner_kind, owner_id, dream_run_id, finding_type="stuck_wake_job", severity="warning", target_kind="wake_job", target_id=row["id"], message=f"Wake job {row['id']} is still running.", metadata=dict(row)))
    except Exception:
        pass
    try:
        pending = conn.execute("SELECT COUNT(*) FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending'", (owner_kind, owner_id)).fetchone()[0]
        if pending:
            findings.append(_insert_finding(conn, owner_kind, owner_id, dream_run_id, finding_type="pending_delayed_replies", severity="info", target_kind="reply_gate", message=f"There are {int(pending)} delayed replies waiting after sleep.", proposed_ops=[{"type": "RELEASE_DELAYED_REPLIES", "payload": {"reason": "DreamAudit wake release"}}], metadata={"pending_count": int(pending)}))
    except Exception:
        pass

    # 4. Reserved resources tied to terminal events.
    try:
        rows = conn.execute(
            """SELECT r.* FROM resource_reservations r
                  LEFT JOIN events e ON e.id=r.event_id
                 WHERE r.owner_kind=? AND r.owner_id=? AND r.status='reserved'
                   AND e.status IN ('completed','cancelled','failed','abandoned','archived','discarded')
                 LIMIT 20""",
            (owner_kind, owner_id),
        ).fetchall()
        for row in rows:
            findings.append(_insert_finding(conn, owner_kind, owner_id, dream_run_id, finding_type="stale_resource_reservation", severity="warning", target_kind="resource_reservation", target_id=row["id"], message=f"Reservation {row['id']} is still reserved after its event closed.", proposed_ops=[{"type": "RESOURCE_RELEASE", "payload": {"reservation_id": row["id"]}}], metadata=dict(row)))
    except Exception:
        pass

    summary = {"finding_count": len(findings), "errors": len([f for f in findings if f.get("severity") == "error"]), "warnings": len([f for f in findings if f.get("severity") == "warning"]), "infos": len([f for f in findings if f.get("severity") == "info"])}
    conn.execute("UPDATE dream_runs SET audit_status=?, findings_count=?, audit_summary_json=?, updated_at=datetime('now') WHERE id=?", ("ok" if not findings else "findings", len(findings), dumps(summary), dream_run_id))
    append_journal(conn, owner_kind, owner_id, "dream_audit_completed", {"dream_run_id": dream_run_id, **summary}, "dream")
    return {"ok": True, "summary": summary, "findings": findings}


def _recent_context(conn, owner_kind: str, owner_id: str, limit: int = 6) -> dict[str, Any]:
    memories = conn.execute("SELECT id, memory_type, content, importance, emotional_weight FROM memories WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    events = conn.execute("SELECT id,title,event_category,status,importance,progress FROM events WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    goals = []
    try:
        goals = conn.execute("SELECT id,title,status,progress,priority FROM goals WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, int(max(3, limit // 2)))).fetchall()
    except Exception:
        goals = []
    return {
        "memories": [dict(r) for r in memories],
        "events": [dict(r) for r in events],
        "goals": [dict(r) for r in goals],
    }


def _compose_dream_text(ctx: dict[str, Any], findings: list[dict[str, Any]], session: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    memories = ctx.get("memories") or []
    events = ctx.get("events") or []
    goals = ctx.get("goals") or []
    duration = session.get("actual_duration_minutes") if session else None
    quality = session.get("quality_score") if session else None
    mem_fragments = [str(m.get("content") or "")[:48] for m in memories[:3] if str(m.get("content") or "").strip()]
    event_titles = [str(e.get("title") or "")[:32] for e in events[:3] if e.get("title")]
    goal_titles = [str(g.get("title") or "")[:32] for g in goals[:2] if g.get("title")]
    symbols = ["账页", "时钟", "门", "灯"]
    if findings:
        symbols.append("未合上的抽屉")
    if goal_titles:
        symbols.append("远处的路标")
    if event_titles:
        symbols.append("排好的格子")
    first_memory = mem_fragments[0] if mem_fragments else "最近的零散日常"
    first_event = event_titles[0] if event_titles else "今天的安排"
    goal_part = f"，远处还有写着『{goal_titles[0]}』的路标" if goal_titles else ""
    audit_part = "梦里我还顺手检查了几只没合上的抽屉" if findings else "梦里那些抽屉都合上了"
    sleep_part = f"睡了大约 {duration} 分钟" if duration is not None else "这一觉"
    if quality is not None:
        sleep_part += f"，睡眠质量约 {round(float(quality), 2)}"
    content = (
        f"我像是在一间安静的资料室里醒着做梦。桌上摊着一本生活账页，第一页写着『{first_memory}』，"
        f"旁边的时钟把『{first_event}』拆成一格一格的光{goal_part}。{audit_part}，"
        f"确认没有什么东西完全掉出生活线。醒来时我记得最清楚的是：{sleep_part}，梦的感觉像一次温和的自检。"
    )
    share = "我醒来前做了一个像 LifeEngine 自检一样的梦：它把最近的记忆、日程和资源账页都摊开检查了一遍。"
    if findings:
        share += f"梦后自检发现 {len(findings)} 个需要留意的状态点，我已经记到 trace 里了。"
    else:
        share += "这次梦后自检没有发现明显漏结算。"
    return content, share, symbols


def create_dream_entry(conn, owner_kind: str, owner_id: str, *, dream_run_id: str | None = None,
                       sleep_session_id: str | None = None, content: str | None = None,
                       summary: str | None = None, share_text: str | None = None,
                       symbols: list[str] | None = None, source_memory_ids: list[str] | None = None,
                       source_event_ids: list[str] | None = None, source_goal_ids: list[str] | None = None,
                       source_finding_ids: list[str] | None = None, truth_layer: str = "dream_symbolic",
                       privacy: str = "safe_to_share", status: str = "created", source: str = "dream") -> dict[str, Any]:
    if not content or not str(content).strip():
        raise ValueError("dream content is required")
    entry_id = new_id("dreamentry")
    conn.execute(
        """INSERT INTO dream_entries(
             id, owner_kind, owner_id, dream_run_id, sleep_session_id, content, summary, share_text,
             symbols_json, source_memory_ids_json, source_event_ids_json, source_goal_ids_json,
             source_finding_ids_json, truth_layer, privacy, status
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (entry_id, owner_kind, owner_id, dream_run_id, sleep_session_id, str(content).strip(), summary, share_text,
         dumps(symbols or []), dumps(source_memory_ids or []), dumps(source_event_ids or []), dumps(source_goal_ids or []),
         dumps(source_finding_ids or []), truth_layer, privacy, status),
    )
    mem = None
    try:
        mem = create_memory(conn, owner_kind, owner_id, f"梦：{summary or share_text or content}", memory_type="dream", source=source, importance=55, emotional_weight=10, confidence=0.8)
    except Exception as exc:
        mem = {"error": f"{type(exc).__name__}: {exc}"}
    conn.execute("UPDATE dream_entries SET memory_id=? WHERE id=?", (mem.get("id") if isinstance(mem, dict) else None, entry_id))
    if dream_run_id:
        conn.execute("UPDATE dream_runs SET created_entry_id=?, narrative_status='created', updated_at=datetime('now') WHERE id=?", (entry_id, dream_run_id))
    append_journal(conn, owner_kind, owner_id, "dream_entry_created", {"dream_entry_id": entry_id, "dream_run_id": dream_run_id, "truth_layer": truth_layer}, source)
    out = get_dream_entry(conn, entry_id)
    out["memory"] = mem
    return out


def run_dream_cycle(conn, owner_kind: str, owner_id: str, *, sleep_session_id: str | None = None,
                    force: bool = False, allow_nap: bool | None = None, create_share_intent: bool = True,
                    target_user_id: str | None = None, trigger: str = "sleep_wake", source: str = "dream", trace_id: str | None = None,
                    **_: Any) -> dict[str, Any]:
    policy = _active_dream_policy(conn, owner_kind, owner_id)
    srd_policy = get_srd_policy(conn, owner_kind, owner_id).get("effective_policy", {})
    policy = {**(srd_policy.get("dream") or {}), **policy}
    if allow_nap is None:
        allow_nap = bool(policy.get("allow_nap_dreams", False))
    create_share_intent = bool(create_share_intent and policy.get("share_on_wake", True) and policy.get("share_mode", "pending_intent") != "self_journal")
    target_user_id = target_user_id or str(policy.get("default_share_user_id") or "anonymous-user")

    if sleep_session_id:
        session_row = conn.execute("SELECT * FROM sleep_sessions WHERE id=? AND owner_kind=? AND owner_id=?", (sleep_session_id, owner_kind, owner_id)).fetchone()
        if not session_row:
            raise ValueError(f"sleep session not found: {sleep_session_id}")
        session = dict(session_row)
    else:
        session = _latest_sleep_session_without_dream(conn, owner_kind, owner_id)
        if not session:
            raise ValueError("no completed/interrupted sleep session without DreamRun found")
        sleep_session_id = session["id"]

    existing = conn.execute("SELECT * FROM dream_runs WHERE owner_kind=? AND owner_id=? AND sleep_session_id=? ORDER BY created_at DESC LIMIT 1", (owner_kind, owner_id, sleep_session_id)).fetchone()
    if existing and not force:
        run = get_dream_run(conn, existing["id"])
        return {"ok": True, "dream_run": run, "already_ran": True}

    duration = int(session.get("actual_duration_minutes") or 0)
    session_type = str(session.get("session_type") or "core_sleep")
    min_minutes = int(policy.get("min_core_dream_minutes", MIN_CORE_DREAM_MINUTES))
    should_skip = (session_type != "core_sleep" and not allow_nap) or (duration and duration < min_minutes and not force)
    run_id = new_id("dreamrun")
    start = now_iso()
    conn.execute(
        """INSERT INTO dream_runs(
             id, owner_kind, owner_id, sleep_session_id, sleep_plan_id, run_type, status, started_at,
             trigger, audit_status, narrative_status, memory_consolidation_status, share_status,
             trace_id, metadata_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, sleep_session_id, session.get("sleep_plan_id"), "sleep_dream", "running", start,
         trigger, "pending", "pending", "pending", "pending", trace_id, dumps({"session_type": session_type, "duration": duration, "policy": policy})),
    )
    append_journal(conn, owner_kind, owner_id, "dream_run_started", {"dream_run_id": run_id, "sleep_session_id": sleep_session_id, "trigger": trigger}, source)

    if should_skip:
        reason = "nap dreams disabled" if session_type != "core_sleep" and not allow_nap else f"sleep duration {duration}m below dream threshold {min_minutes}m"
        finding = _insert_finding(conn, owner_kind, owner_id, run_id, finding_type="dream_skipped", severity="info", target_kind="sleep_session", target_id=sleep_session_id, message=reason, metadata={"duration": duration, "session_type": session_type})
        conn.execute("UPDATE dream_runs SET status='skipped', completed_at=datetime('now'), audit_status='skipped', narrative_status='skipped', memory_consolidation_status='skipped', share_status='skipped', findings_count=1, audit_summary_json=?, updated_at=datetime('now') WHERE id=?", (dumps({"skipped_reason": reason}), run_id))
        return {"ok": True, "dream_run": get_dream_run(conn, run_id), "skipped": True, "reason": reason, "finding": finding}

    audit = run_dream_audit(conn, owner_kind, owner_id, run_id, sleep_session=session)
    findings = audit.get("findings") or []
    ctx = _recent_context(conn, owner_kind, owner_id, limit=int(policy.get("memory_window", 6)))
    content, share_text, symbols = _compose_dream_text(ctx, findings, session)
    share_text = render_dream_share({"effective_policy": srd_policy}, summary=share_text)
    source_memory_ids = [m.get("id") for m in (ctx.get("memories") or []) if m.get("id")]
    source_event_ids = [e.get("id") for e in (ctx.get("events") or []) if e.get("id")]
    source_goal_ids = [g.get("id") for g in (ctx.get("goals") or []) if g.get("id")]
    source_finding_ids = [f.get("id") for f in findings if f.get("id")]
    entry = create_dream_entry(
        conn, owner_kind, owner_id, dream_run_id=run_id, sleep_session_id=sleep_session_id,
        content=content, summary="睡眠中的 LifeEngine 自检梦", share_text=share_text,
        symbols=symbols, source_memory_ids=source_memory_ids, source_event_ids=source_event_ids,
        source_goal_ids=source_goal_ids, source_finding_ids=source_finding_ids, source=source,
    )
    proactive_intent = None
    if create_share_intent and owner_kind == "agent":
        proactive_intent = create_proactive_intent(
            conn, owner_id,
            target_type="user", target_id=target_user_id,
            intent_type="self_reflection_share", summary=share_text,
            emotional_tone="calm", importance=55 if not findings else 68,
            urgency=35, novelty=70, relationship_relevance=60,
            privacy_level="safe_to_share", status="generated",
            generated_by="dream", source="dream",
            delivery_policy={"canPush": False, "canMentionNextTurn": True, "quietHoursRespect": True},
            trigger_event_id=session.get("event_id"), trace_id=trace_id,
        )
        conn.execute("UPDATE dream_runs SET proactive_intent_id=?, share_status='intent_generated' WHERE id=?", (proactive_intent.get("id"), run_id))
    else:
        conn.execute("UPDATE dream_runs SET share_status='not_requested' WHERE id=?", (run_id,))

    conn.execute("UPDATE dream_runs SET status='completed', completed_at=datetime('now'), memory_consolidation_status='completed', updated_at=datetime('now'), narrative_inputs_json=? WHERE id=?", (dumps(ctx), run_id))
    append_journal(conn, owner_kind, owner_id, "dream_run_completed", {"dream_run_id": run_id, "entry_id": entry.get("id"), "finding_count": len(findings), "proactive_intent_id": proactive_intent.get("id") if proactive_intent else None}, source)
    return {"ok": True, "dream_run": get_dream_run(conn, run_id), "entry": entry, "audit": audit, "proactive_intent": proactive_intent}


def dream_status(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    latest = list_dream_runs(conn, owner_kind, owner_id, limit=5)
    entries = list_dream_entries(conn, owner_kind, owner_id, limit=5)
    findings = list_dream_findings(conn, owner_kind, owner_id, limit=5)
    return {"recent_runs": latest, "recent_entries": entries, "recent_findings": findings}


# ---------------------------------------------------------------------------
# v0.11.5 DreamAudit repair policy
# ---------------------------------------------------------------------------

DEFAULT_SAFE_DREAM_REPAIR_TYPES = ["stale_schedule_block", "pending_delayed_replies", "stale_resource_reservation"]

def get_dream_repair_policy(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM dream_repair_policies WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone()
    if not row:
        conn.execute(
            "INSERT OR IGNORE INTO dream_repair_policies(owner_kind, owner_id, mode, safe_finding_types_json, updated_by) VALUES(?,?,?,?,?)",
            (owner_kind, owner_id, "manual", dumps(DEFAULT_SAFE_DREAM_REPAIR_TYPES), "default"),
        )
        row = conn.execute("SELECT * FROM dream_repair_policies WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone()
    d = dict(row) if row else {"mode": "manual", "safe_finding_types_json": dumps(DEFAULT_SAFE_DREAM_REPAIR_TYPES), "auto_apply_limit": 10}
    d["safe_finding_types"] = loads(d.pop("safe_finding_types_json"), DEFAULT_SAFE_DREAM_REPAIR_TYPES)
    return d

def set_dream_repair_policy(conn, owner_kind: str, owner_id: str, *, mode: str = "manual", safe_finding_types: list[str] | None = None,
                            auto_apply_limit: int = 10, updated_by: str = "life_dream_tool") -> dict[str, Any]:
    if mode not in {"off", "manual", "auto_safe"}:
        raise ValueError("dream repair policy mode must be off, manual, or auto_safe")
    safe = safe_finding_types or DEFAULT_SAFE_DREAM_REPAIR_TYPES
    conn.execute(
        """INSERT INTO dream_repair_policies(owner_kind, owner_id, mode, safe_finding_types_json, auto_apply_limit, updated_by)
             VALUES(?,?,?,?,?,?)
             ON CONFLICT(owner_kind, owner_id) DO UPDATE SET
               mode=excluded.mode,
               safe_finding_types_json=excluded.safe_finding_types_json,
               auto_apply_limit=excluded.auto_apply_limit,
               updated_by=excluded.updated_by,
               updated_at=datetime('now')""",
        (owner_kind, owner_id, mode, dumps(safe), int(auto_apply_limit), updated_by),
    )
    append_journal(conn, owner_kind, owner_id, "dream_repair_policy_updated", {"mode": mode, "safe_finding_types": safe, "auto_apply_limit": auto_apply_limit}, updated_by)
    return get_dream_repair_policy(conn, owner_kind, owner_id)

# ---------------------------------------------------------------------------
# v0.11.4 DreamAudit repair planning/status helpers
# ---------------------------------------------------------------------------

def list_dream_repair_runs(conn, owner_kind: str, owner_id: str, dream_run_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if dream_run_id:
        clause += " AND dream_run_id=?"
        params.append(dream_run_id)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM dream_repair_runs WHERE {clause} ORDER BY created_at DESC LIMIT ?", tuple(params)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["finding_ids"] = loads(d.pop("finding_ids_json", "[]"), [])
        d["proposed_ops"] = loads(d.pop("proposed_ops_json", "[]"), [])
        d["output"] = loads(d.pop("output_json", "{}"), {})
        out.append(d)
    return out


def collect_open_dream_repair_ops(conn, owner_kind: str, owner_id: str, *, dream_run_id: str | None = None,
                                  finding_ids: list[str] | None = None, limit: int = 50,
                                  policy_mode: str | None = None) -> dict[str, Any]:
    """Collect proposed LifeOps from open DreamAudit findings.

    This is intentionally a planning helper: it does not apply repairs itself.
    Runtime owns the transaction and validation path so fixes still go through
    LifeOps, receipts, journal, and trace.
    """
    policy = get_dream_repair_policy(conn, owner_kind, owner_id)
    mode = policy_mode or policy.get("mode", "manual")
    if mode == "off":
        return {"ok": True, "dream_run_id": dream_run_id, "finding_count": 0, "findings": [], "ops": [], "policy": policy, "policy_blocked": True}
    safe_types = set(policy.get("safe_finding_types") or DEFAULT_SAFE_DREAM_REPAIR_TYPES)
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=? AND status='open'"
    if dream_run_id:
        clause += " AND dream_run_id=?"
        params.append(dream_run_id)
    rows = conn.execute(f"SELECT * FROM dream_audit_findings WHERE {clause} ORDER BY created_at ASC LIMIT ?", tuple(params + [int(limit)])).fetchall()
    wanted = set(finding_ids or [])
    findings: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = []
    for row in rows:
        d = _decode_row(row)
        if wanted and d.get("id") not in wanted:
            continue
        if mode == "auto_safe" and d.get("finding_type") not in safe_types:
            continue
        proposed = d.get("proposed_ops") or []
        if proposed:
            findings.append(d)
            ops.extend(proposed)
            if mode == "auto_safe" and len(findings) >= int(policy.get("auto_apply_limit") or 10):
                break
    return {"ok": True, "dream_run_id": dream_run_id, "finding_count": len(findings), "findings": findings, "ops": ops, "policy": policy, "policy_mode": mode}


def record_dream_repair_run(conn, owner_kind: str, owner_id: str, *, dream_run_id: str | None, mode: str,
                            finding_ids: list[str], proposed_ops: list[dict[str, Any]], status: str,
                            transaction_id: str | None = None, receipt_id: str | None = None,
                            output: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    repair_id = new_id("dreamrepair")
    conn.execute(
        """INSERT INTO dream_repair_runs(
             id, owner_kind, owner_id, dream_run_id, mode, status, finding_ids_json,
             proposed_ops_json, transaction_id, receipt_id, error, output_json, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (repair_id, owner_kind, owner_id, dream_run_id, mode, status, dumps(finding_ids), dumps(proposed_ops),
         transaction_id, receipt_id, error, dumps(output or {})),
    )
    if transaction_id and finding_ids:
        for fid in finding_ids:
            conn.execute("UPDATE dream_audit_findings SET status='resolved', resolved_by_tx_id=? WHERE id=? AND owner_kind=? AND owner_id=?", (transaction_id, fid, owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "dream_audit_repair_run", {"dream_repair_run_id": repair_id, "dream_run_id": dream_run_id, "status": status, "transaction_id": transaction_id, "finding_ids": finding_ids}, "dream_repair")
    row = conn.execute("SELECT * FROM dream_repair_runs WHERE id=?", (repair_id,)).fetchone()
    return list_dream_repair_runs(conn, owner_kind, owner_id, dream_run_id=None, limit=1)[0] if row else {"id": repair_id, "status": status}
