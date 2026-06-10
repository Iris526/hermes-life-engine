"""Goals, Life Arcs, Event Decomposition, and Reflection operations."""

from __future__ import annotations

from typing import Any

from .events import create_event, create_schedule_block, get_event
from .jsonutil import dumps, loads
from .memory import create_memory
from .time_utils import normalized_iso, to_epoch
from .trace import append_journal, new_id


def _clamp_progress(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _parse_goal(row) -> dict[str, Any]:
    d = dict(row)
    d["related_event_ids"] = loads(d.pop("related_event_ids_json"), [])
    d["metrics"] = loads(d.pop("metrics_json"), {})
    return d


def _parse_arc(row) -> dict[str, Any]:
    d = dict(row)
    d["theme"] = loads(d.pop("theme_json"), {})
    return d


def create_life_arc(conn, owner_kind: str, owner_id: str, title: str, description: str | None = None,
                    arc_type: str = "lifestyle", status: str = "active", theme: dict[str, Any] | None = None,
                    start_date: str | None = None, end_date: str | None = None, current_phase: str | None = None,
                    stage: str | None = None, goal_id: str | None = None,
                    progress: float = 0, priority: int = 50, canon_version: int | None = None,
                    **_ignored: Any) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("life arc title is required")
    arc_id = new_id("arc")
    conn.execute(
        """INSERT INTO life_arcs(id, owner_kind, owner_id, title, description, arc_type, status,
               theme_json, start_date, end_date, current_phase, progress, priority, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (arc_id, owner_kind, owner_id, title.strip(), description, arc_type, status,
         dumps(theme or {}), start_date, end_date, current_phase or stage, _clamp_progress(progress), int(priority), canon_version),
    )
    if goal_id:
        conn.execute("UPDATE goals SET arc_id=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?", (arc_id, goal_id, owner_kind, owner_id))
    append_journal(conn, owner_kind, owner_id, "life_arc_created", {"arc_id": arc_id, "title": title, "goal_id": goal_id}, "goal", canon_version=canon_version)
    if goal_id:
        _refresh_arc_progress(conn, owner_kind, owner_id, arc_id)
    return get_life_arc(conn, owner_kind, owner_id, arc_id)


def get_life_arc(conn, owner_kind: str, owner_id: str, arc_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM life_arcs WHERE id=? AND owner_kind=? AND owner_id=?", (arc_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"life arc not found: {arc_id}")
    return _parse_arc(row)


def list_life_arcs(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute("SELECT * FROM life_arcs WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, status, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM life_arcs WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_parse_arc(r) for r in rows]


def create_goal(conn, owner_kind: str, owner_id: str, title: str, description: str | None = None,
                goal_type: str = "lifestyle", status: str = "active", priority: int = 50,
                progress: float = 0, target_date: str | None = None, arc_id: str | None = None,
                metrics: dict[str, Any] | None = None, canon_version: int | None = None,
                **_ignored: Any) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("goal title is required")
    if arc_id:
        get_life_arc(conn, owner_kind, owner_id, arc_id)
    target_date_iso = normalized_iso(target_date) if target_date else None
    target_date_ts = to_epoch(target_date_iso) if target_date_iso else None
    goal_id = new_id("goal")
    prog = _clamp_progress(progress)
    status = "completed" if prog >= 100 else status
    completed_at_sql = "datetime('now')" if status == "completed" else "NULL"
    conn.execute(
        f"""INSERT INTO goals(id, owner_kind, owner_id, arc_id, title, description, goal_type, status,
               priority, progress, target_date, target_date_ts, metrics_json, canon_version, completed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,{completed_at_sql})""",
        (goal_id, owner_kind, owner_id, arc_id, title.strip(), description, goal_type, status,
         int(priority), prog, target_date_iso, target_date_ts, dumps(metrics or {}), canon_version),
    )
    append_journal(conn, owner_kind, owner_id, "goal_created", {"goal_id": goal_id, "title": title, "arc_id": arc_id}, "goal", canon_version=canon_version)
    if arc_id:
        _refresh_arc_progress(conn, owner_kind, owner_id, arc_id)
    return get_goal(conn, owner_kind, owner_id, goal_id)


def get_goal(conn, owner_kind: str, owner_id: str, goal_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM goals WHERE id=? AND owner_kind=? AND owner_id=?", (goal_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"goal not found: {goal_id}")
    return _parse_goal(row)


def list_goals(conn, owner_kind: str, owner_id: str, status: str | None = None, arc_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?"]
    params: list[Any] = [owner_kind, owner_id]
    if status:
        clauses.append("status=?")
        params.append(status)
    if arc_id:
        clauses.append("arc_id=?")
        params.append(arc_id)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM goals WHERE {' AND '.join(clauses)} ORDER BY priority DESC, updated_at DESC LIMIT ?", params).fetchall()
    return [_parse_goal(r) for r in rows]


def compute_goal_progress(conn, owner_kind: str, owner_id: str, goal_id: str) -> dict[str, Any]:
    get_goal(conn, owner_kind, owner_id, goal_id)
    links = conn.execute(
        """SELECT l.*, e.status, e.progress, e.title FROM event_goal_links l
              JOIN events e ON e.id=l.event_id
             WHERE l.owner_kind=? AND l.owner_id=? AND l.goal_id=? AND l.role!='parent'""",
        (owner_kind, owner_id, goal_id),
    ).fetchall()
    if not links:
        goal = get_goal(conn, owner_kind, owner_id, goal_id)
        return {"goal_id": goal_id, "computed_progress": float(goal.get("progress") or 0), "completed_weight": 0, "total_weight": 0, "links": []}
    total = 0.0
    done = 0.0
    out = []
    for row in links:
        weight = float(row["weight"] or 1.0)
        total += weight
        status = row["status"]
        contribution = weight if status == "completed" else weight * max(0.0, min(100.0, float(row["progress"] or 0))) / 100.0
        done += contribution
        out.append({"event_id": row["event_id"], "title": row["title"], "status": status, "weight": weight, "contribution": contribution})
    progress = round((done / total) * 100, 6) if total else 0.0
    if isinstance(progress, float) and progress.is_integer():
        progress = int(progress)
    return {"goal_id": goal_id, "computed_progress": progress, "completed_weight": done, "total_weight": total, "links": out}


def update_goal_progress(conn, owner_kind: str, owner_id: str, goal_id: str,
                         progress_delta: float | None = None, progress: float | None = None,
                         reason: str | None = None, event_id: str | None = None,
                         result_id: str | None = None, source: str = "life_commit") -> dict[str, Any]:
    goal = get_goal(conn, owner_kind, owner_id, goal_id)
    computed = None
    if progress is None and progress_delta is None:
        computed = compute_goal_progress(conn, owner_kind, owner_id, goal_id)
        progress = computed["computed_progress"]
    elif progress is None:
        progress = float(goal["progress"]) + float(progress_delta or 0)
    new_progress = _clamp_progress(progress)
    status = "completed" if new_progress >= 100 else goal["status"]
    completed_at_sql = ", completed_at=datetime('now')" if status == "completed" and goal.get("completed_at") is None else ""
    conn.execute(f"UPDATE goals SET progress=?, status=?, updated_at=datetime('now'){completed_at_sql} WHERE id=? AND owner_kind=? AND owner_id=?", (new_progress, status, goal_id, owner_kind, owner_id))
    entry_id = new_id("gprog")
    conn.execute(
        """INSERT INTO goal_progress_entries(id, owner_kind, owner_id, goal_id, delta, progress_after,
               reason, event_id, result_id, source) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (entry_id, owner_kind, owner_id, goal_id, None if progress_delta is None else float(progress_delta), new_progress, reason, event_id, result_id, source),
    )
    append_journal(conn, owner_kind, owner_id, "goal_progress_updated", {"goal_id": goal_id, "progress_after": new_progress, "reason": reason, "computed": computed}, source)
    updated = get_goal(conn, owner_kind, owner_id, goal_id)
    if updated.get("arc_id"):
        _refresh_arc_progress(conn, owner_kind, owner_id, updated["arc_id"])
    return {"goal": updated, "progress_entry_id": entry_id, "computed": computed}


def link_event_to_goal(conn, owner_kind: str, owner_id: str, goal_id: str, event_id: str,
                       role: str = "supports", weight: float = 1.0, source: str = "life_commit", **_ignored: Any) -> dict[str, Any]:
    goal = get_goal(conn, owner_kind, owner_id, goal_id)
    event = get_event(conn, event_id)
    if event["owner_kind"] != owner_kind or event["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    link_id = new_id("eglink")
    conn.execute("""INSERT OR IGNORE INTO event_goal_links(id, owner_kind, owner_id, event_id, goal_id, role, weight, source)
              VALUES(?,?,?,?,?,?,?,?)""", (link_id, owner_kind, owner_id, event_id, goal_id, role, float(weight), source))
    row = conn.execute("SELECT * FROM event_goal_links WHERE owner_kind=? AND owner_id=? AND event_id=? AND goal_id=? AND role=?", (owner_kind, owner_id, event_id, goal_id, role)).fetchone()
    ids = goal.get("related_event_ids", [])
    if event_id not in ids:
        ids.append(event_id)
        conn.execute("UPDATE goals SET related_event_ids_json=?, updated_at=datetime('now') WHERE id=?", (dumps(ids), goal_id))
    append_journal(conn, owner_kind, owner_id, "event_goal_linked", {"goal_id": goal_id, "event_id": event_id, "role": role}, source)
    return dict(row)


def create_event_dependency(conn, owner_kind: str, owner_id: str, event_id: str, depends_on_event_id: str,
                            dependency_type: str = "finish_to_start", status: str = "active") -> dict[str, Any]:
    event = get_event(conn, event_id)
    dep = get_event(conn, depends_on_event_id)
    if event["owner_kind"] != owner_kind or event["owner_id"] != owner_id or dep["owner_kind"] != owner_kind or dep["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    dep_id = new_id("dep")
    conn.execute("""INSERT OR IGNORE INTO event_dependencies(id, owner_kind, owner_id, event_id, depends_on_event_id, dependency_type, status)
              VALUES(?,?,?,?,?,?,?)""", (dep_id, owner_kind, owner_id, event_id, depends_on_event_id, dependency_type, status))
    row = conn.execute("""SELECT * FROM event_dependencies WHERE owner_kind=? AND owner_id=? AND event_id=? AND depends_on_event_id=? AND dependency_type=?""", (owner_kind, owner_id, event_id, depends_on_event_id, dependency_type)).fetchone()
    append_journal(conn, owner_kind, owner_id, "event_dependency_created", {"event_id": event_id, "depends_on_event_id": depends_on_event_id, "dependency_type": dependency_type}, "goal")
    return dict(row)


def create_milestone(conn, owner_kind: str, owner_id: str, goal_id: str, title: str,
                     description: str | None = None, target_progress: float | None = None,
                     due_at: str | None = None, status: str = "planned", **_ignored: Any) -> dict[str, Any]:
    get_goal(conn, owner_kind, owner_id, goal_id)
    if not title or not title.strip():
        raise ValueError("milestone title is required")
    due_iso = normalized_iso(due_at) if due_at else None
    due_ts = to_epoch(due_iso) if due_iso else None
    ms_id = new_id("mile")
    conn.execute(
        """INSERT INTO goal_milestones(id, owner_kind, owner_id, goal_id, title, description,
              target_progress, due_at, due_at_ts, status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (ms_id, owner_kind, owner_id, goal_id, title.strip(), description, target_progress, due_iso, due_ts, status),
    )
    append_journal(conn, owner_kind, owner_id, "goal_milestone_created", {"milestone_id": ms_id, "goal_id": goal_id, "title": title}, "goal")
    return dict(conn.execute("SELECT * FROM goal_milestones WHERE id=?", (ms_id,)).fetchone())


def list_milestones(conn, owner_kind: str, owner_id: str, goal_id: str | None = None,
                    status: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?"]
    params: list[Any] = [owner_kind, owner_id]
    if goal_id:
        clauses.append("goal_id=?")
        params.append(goal_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM goal_milestones WHERE {' AND '.join(clauses)} ORDER BY COALESCE(due_at_ts, unixepoch(created_at)) ASC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def list_event_dependencies(conn, owner_kind: str, owner_id: str, parent_event_id: str | None = None,
                            child_event_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?", "status='active'"]
    params: list[Any] = [owner_kind, owner_id]
    if parent_event_id:
        clauses.append("depends_on_event_id=?")
        params.append(parent_event_id)
    if child_event_id:
        clauses.append("event_id=?")
        params.append(child_event_id)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM event_dependencies WHERE {' AND '.join(clauses)} ORDER BY created_at ASC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def decompose_event(conn, owner_kind: str, owner_id: str, parent_event_id: str, children: list[dict[str, Any]],
                    goal_id: str | None = None, decomposition_type: str = "manual", strategy: str = "children",
                    source: str = "life_commit", canon_version: int | None = None,
                    link_children_to_goal: bool = True, sequential_dependencies: bool = False,
                    **_ignored: Any) -> dict[str, Any]:
    parent = get_event(conn, parent_event_id)
    if parent["owner_kind"] != owner_kind or parent["owner_id"] != owner_id:
        raise ValueError("parent event owner mismatch")
    if goal_id:
        get_goal(conn, owner_kind, owner_id, goal_id)
        link_event_to_goal(conn, owner_kind, owner_id, goal_id, parent_event_id, role="parent", weight=1.0, source=source)
    child_ids: list[str] = []
    children_out: list[dict[str, Any]] = []
    dep_ids: list[str] = []
    deps_out: list[dict[str, Any]] = []
    weights: dict[str, float] = {}
    previous_child_id: str | None = None
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("each child must be an object")
        child_payload = dict(child)
        schedule_payload = child_payload.pop("schedule", None) or {}
        child_goal_id = child_payload.pop("goal_id", None) or goal_id
        weight = float(child_payload.pop("progress_weight", child_payload.pop("weight", 1.0)))
        child_payload.setdefault("status", "planned")
        child_payload.setdefault("event_type", parent.get("event_type") or "other")
        child_payload.setdefault("source", source)
        child_payload["parent_event_id"] = parent_event_id
        created = create_event(conn, owner_kind, owner_id, canon_version=canon_version, **child_payload)
        child_ids.append(created["id"])
        children_out.append(created)
        weights[created["id"]] = weight
        # Each child depends on the parent event as its owning/decomposition parent.
        parent_dep = create_event_dependency(conn, owner_kind, owner_id, created["id"], parent_event_id, "parent_child")
        dep_ids.append(parent_dep["id"])
        deps_out.append(parent_dep)
        if child_goal_id and link_children_to_goal:
            link_event_to_goal(conn, owner_kind, owner_id, child_goal_id, created["id"], role="child", weight=weight, source=source)
        if previous_child_id and sequential_dependencies:
            dep = create_event_dependency(conn, owner_kind, owner_id, created["id"], previous_child_id, "finish_to_start")
            dep_ids.append(dep["id"])
            deps_out.append(dep)
        explicit_deps = child_payload.get("depends_on_event_ids") or child.get("depends_on_event_ids") or []
        for dep_event_id in explicit_deps:
            dep = create_event_dependency(conn, owner_kind, owner_id, created["id"], dep_event_id, "finish_to_start")
            dep_ids.append(dep["id"])
            deps_out.append(dep)
        if schedule_payload.get("start") and schedule_payload.get("end"):
            schedule_payload.setdefault("event_id", created["id"])
            create_schedule_block(conn, owner_kind, owner_id, **schedule_payload)
        previous_child_id = created["id"]
    decomp_id = new_id("decomp")
    conn.execute("""INSERT INTO event_decompositions(id, owner_kind, owner_id, parent_event_id, goal_id,
               decomposition_type, strategy, child_event_ids_json, dependency_ids_json, weights_json, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (decomp_id, owner_kind, owner_id, parent_event_id, goal_id, decomposition_type, strategy, dumps(child_ids), dumps(dep_ids), dumps(weights), source))
    conn.execute("UPDATE events SET status=CASE WHEN status='draft' THEN 'planned' ELSE status END, updated_at=datetime('now') WHERE id=?", (parent_event_id,))
    append_journal(conn, owner_kind, owner_id, "event_decomposed", {"decomposition_id": decomp_id, "parent_event_id": parent_event_id, "child_event_ids": child_ids, "goal_id": goal_id}, source, canon_version=canon_version)
    return {"decomposition_id": decomp_id, "parent_event_id": parent_event_id, "child_event_ids": child_ids, "children": children_out, "dependency_ids": dep_ids, "dependencies": deps_out, "weights": weights}


def recompute_parent_event_progress(conn, owner_kind: str, owner_id: str, event_id: str, source: str = "life_commit") -> dict[str, Any]:
    parent = get_event(conn, event_id)
    if parent["owner_kind"] != owner_kind or parent["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    row = conn.execute("SELECT * FROM event_decompositions WHERE owner_kind=? AND owner_id=? AND parent_event_id=? ORDER BY created_at DESC LIMIT 1", (owner_kind, owner_id, event_id)).fetchone()
    if not row:
        return {"event": parent, "computed_progress": float(parent.get("progress") or 0), "reason": "no decomposition"}
    decomp = dict(row)
    child_ids = loads(decomp.get("child_event_ids_json"), [])
    weights = loads(decomp.get("weights_json"), {})
    total = 0.0
    done = 0.0
    children = []
    for cid in child_ids:
        child = get_event(conn, cid)
        weight = float(weights.get(cid, 1.0))
        total += weight
        contribution = weight if child["status"] == "completed" else weight * max(0.0, min(100.0, float(child.get("progress") or 0))) / 100.0
        done += contribution
        children.append({"id": cid, "title": child["title"], "status": child["status"], "weight": weight, "contribution": contribution})
    progress = round((done / total) * 100, 6) if total else 0.0
    if isinstance(progress, float) and progress.is_integer():
        progress = int(progress)
    status_sql = ", status='completed', closed_at=datetime('now')" if progress >= 100 else ""
    conn.execute(f"UPDATE events SET progress=?, updated_at=datetime('now'){status_sql} WHERE id=?", (progress, event_id))
    append_journal(conn, owner_kind, owner_id, "parent_event_progress_recomputed", {"event_id": event_id, "progress": progress}, source)
    return {"event": get_event(conn, event_id), "computed_progress": progress, "children": children}


def create_reflection(conn, owner_kind: str, owner_id: str, content: str,
                      reflection_type: str = "event_review", target_kind: str | None = None,
                      target_id: str | None = None, insights: dict[str, Any] | None = None,
                      proposed_ops: list[dict[str, Any]] | None = None, source: str = "reflection",
                      canon_version: int | None = None, create_memory_entry: bool = True, **_ignored: Any) -> dict[str, Any]:
    if not content or not content.strip():
        raise ValueError("reflection content is required")
    reflection_id = new_id("reflect")
    conn.execute("""INSERT INTO life_reflections(id, owner_kind, owner_id, reflection_type, target_kind, target_id,
               content, insights_json, proposed_ops_json, source, canon_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (reflection_id, owner_kind, owner_id, reflection_type, target_kind, target_id, content.strip(), dumps(insights or {}), dumps(proposed_ops or []), source, canon_version))
    memory_id = None
    if create_memory_entry:
        try:
            mem = create_memory(conn, owner_kind, owner_id, content.strip(), memory_type="reflection", source=source, canon_version=canon_version)
            memory_id = mem.get("id")
        except Exception:
            memory_id = None
    append_journal(conn, owner_kind, owner_id, "reflection_created", {"reflection_id": reflection_id, "target_kind": target_kind, "target_id": target_id, "memory_id": memory_id}, source, canon_version=canon_version)
    d = dict(conn.execute("SELECT * FROM life_reflections WHERE id=?", (reflection_id,)).fetchone())
    d["memory_id"] = memory_id
    return d


def list_reflections(conn, owner_kind: str, owner_id: str, target_kind: str | None = None,
                     target_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if target_kind and target_id:
        rows = conn.execute("SELECT * FROM life_reflections WHERE owner_kind=? AND owner_id=? AND target_kind=? AND target_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, target_kind, target_id, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM life_reflections WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["insights"] = loads(d.pop("insights_json"), {})
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        out.append(d)
    return out


def _refresh_arc_progress(conn, owner_kind: str, owner_id: str, arc_id: str) -> None:
    rows = conn.execute("SELECT progress FROM goals WHERE owner_kind=? AND owner_id=? AND arc_id=?", (owner_kind, owner_id, arc_id)).fetchall()
    if not rows:
        return
    progress = sum(float(r["progress"] or 0) for r in rows) / len(rows)
    status = "completed" if progress >= 100 else "active"
    completed_at_sql = ", completed_at=datetime('now')" if status == "completed" else ""
    conn.execute(f"UPDATE life_arcs SET progress=?, status=?, updated_at=datetime('now'){completed_at_sql} WHERE id=? AND owner_kind=? AND owner_id=?", (_clamp_progress(progress), status, arc_id, owner_kind, owner_id))


def apply_event_goal_contributions(conn, owner_kind: str, owner_id: str, event_id: str,
                                   source: str = "heartbeat") -> list[dict[str, Any]]:
    """Apply goal progress contributions once for a completed event.

    In v0.6, event_goal_links.weight acts as a progress contribution when the
    linked event reaches completed. applied_at makes this idempotent across
    repeated heartbeat/completion attempts.
    """
    rows = conn.execute(
        """SELECT * FROM event_goal_links WHERE owner_kind=? AND owner_id=? AND event_id=?
              AND applied_at IS NULL AND role!='parent'""",
        (owner_kind, owner_id, event_id),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        delta = float(row["weight"] or 0)
        goal = update_goal_progress(conn, owner_kind, owner_id, row["goal_id"], progress_delta=delta, reason=f"event completed: {event_id}", event_id=event_id, source=source)
        conn.execute("UPDATE event_goal_links SET applied_at=datetime('now') WHERE id=?", (row["id"],))
        out.append({"goal_id": row["goal_id"], "event_id": event_id, "progress_delta": delta, "goal": goal})
    if out:
        append_journal(conn, owner_kind, owner_id, "event_goal_contributions_applied", {"event_id": event_id, "updates": out}, source)
    return out
