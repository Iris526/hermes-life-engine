"""Life Canon migration planning and branch helpers."""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps
from .trace import append_journal, new_id


def _section_changed(old: dict[str, Any], new: dict[str, Any], key: str) -> bool:
    return (old or {}).get(key) != (new or {}).get(key)


def plan_migration(old_canon: dict[str, Any], new_canon: dict[str, Any]) -> dict[str, Any]:
    affected: list[str] = []
    for section in [
        "identity", "worldview", "truth_sources", "resources", "schedule_rules",
        "behavior_rules", "autonomy", "proactive", "diary", "user_life_policy", "agent_life_policy",
    ]:
        if _section_changed(old_canon, new_canon, section):
            affected.append(section)
    if not affected:
        migration_type = "no_state_change"
    elif affected == ["truth_sources"] or set(affected).issubset({"truth_sources", "proactive", "diary", "autonomy"}):
        migration_type = "future_only"
    elif "resources" in affected:
        migration_type = "resource_schema_migration"
    elif "identity" in affected or "worldview" in affected:
        migration_type = "replan_open_events"
    else:
        migration_type = "future_only"
    return {
        "migration_type": migration_type,
        "affected_domains": affected,
        "plan": {
            "open_events": "revalidate" if migration_type in {"replan_open_events", "resource_schema_migration"} else "keep",
            "past_events": "preserve_with_original_canon_version",
            "resources": "ensure_definitions" if "resources" in affected else "keep",
            "heartbeat": "remain_paused_until_resume" if migration_type != "no_state_change" else "no_change",
        },
    }


def record_canon_migration(conn, owner_kind: str, owner_id: str, from_version: int | None,
                           to_version: int, old_canon: dict[str, Any], new_canon: dict[str, Any]) -> dict[str, Any]:
    planned = plan_migration(old_canon, new_canon)
    mid = new_id("migration")
    conn.execute(
        """INSERT INTO canon_migrations(id, owner_kind, owner_id, from_version, to_version, migration_type,
              affected_domains_json, plan_json, status) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            mid, owner_kind, owner_id, from_version, to_version, planned["migration_type"],
            dumps(planned["affected_domains"]), dumps(planned["plan"]), "planned",
        ),
    )
    append_journal(conn, owner_kind, owner_id, "canon_migration_planned", {"migration_id": mid, **planned}, "canon")
    return {"id": mid, **planned, "from_version": from_version, "to_version": to_version, "status": "planned"}


def list_migrations(conn, owner_kind: str, owner_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM canon_migrations WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        import json
        d["affected_domains"] = json.loads(d.pop("affected_domains_json") or "[]")
        d["plan"] = json.loads(d.pop("plan_json") or "{}")
        out.append(d)
    return out


def create_branch(conn, owner_kind: str, owner_id: str, name: str, canon_version: int | None = None,
                  base_branch_id: str | None = None) -> dict[str, Any]:
    branch_id = new_id("branch")
    conn.execute(
        """INSERT INTO life_branches(id, owner_kind, owner_id, name, status, base_branch_id, created_from_canon_version)
              VALUES(?,?,?,?,?,?,?)""",
        (branch_id, owner_kind, owner_id, name, "active", base_branch_id, canon_version),
    )
    append_journal(conn, owner_kind, owner_id, "life_branch_created", {"branch_id": branch_id, "name": name}, "branch")
    return dict(conn.execute("SELECT * FROM life_branches WHERE id=?", (branch_id,)).fetchone())
