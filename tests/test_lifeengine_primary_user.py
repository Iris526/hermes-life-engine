"""轴三-2 regression: the relationship loop resolves ONE user_id.

Before, the producer (life_relationship) defaulted to ``anonymous-user`` while
companion/dream/reflection resolved to canon ``proactive.default_target_user_id``.
So once a real target was configured in production, a note the user shared was
stored under ``anonymous-user`` but looked up under the target — "你讲生活→她
入梦/回访" silently read empty. ``relationship.resolve_primary_user`` unifies all
sides; this pins that a recorded note lands under the resolved user, not
anonymous-user.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from lifeengine import relationship as rel
from lifeengine.canon import ensure_control
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def _setup(rt: LifeEngineRuntime):
    rt.setup("轴三-2 primary-user test agent")
    rt.commit_canon()
    rt.control("resume")


def _set_proactive_target(rt: LifeEngineRuntime, target: str) -> None:
    ctrl = ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID)
    version = ctrl.get("active_canon_version")
    row = rt.conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND version=? AND status='active'",
        (DEFAULT_AGENT_ID, version),
    ).fetchone()
    data = json.loads(row[0]) if row and row[0] else {}
    data.setdefault("proactive", {})["default_target_user_id"] = target
    with rt.conn:
        rt.conn.execute(
            "UPDATE canon_versions SET data_json=? WHERE owner_kind='agent' AND owner_id=? AND version=?",
            (json.dumps(data), DEFAULT_AGENT_ID, version),
        )


def test_resolve_primary_user_precedence(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _setup(rt)
        # No configured target → the shared default.
        assert rel.resolve_primary_user(rt.conn, DEFAULT_AGENT_ID) == rel.DEFAULT_USER_ID
        # An explicit id (a real multi-user sender) always wins.
        assert rel.resolve_primary_user(rt.conn, DEFAULT_AGENT_ID, explicit="alice") == "alice"
        # Canon proactive target becomes the canonical id.
        _set_proactive_target(rt, "ringo")
        assert rel.resolve_primary_user(rt.conn, DEFAULT_AGENT_ID) == "ringo"
    finally:
        rt.close()


def test_recorded_note_lands_under_resolved_user_not_anonymous(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _setup(rt)
        _set_proactive_target(rt, "ringo")
        # Record WITHOUT an explicit user_id — as the tool does when the model
        # doesn't pass one.
        out = rt.relationship("record", content="下周三有个面试", topic="career")
        assert out["ok"]

        under_target = rel.list_relationship_notes(rt.conn, DEFAULT_AGENT_ID, "ringo")
        under_anon = rel.list_relationship_notes(rt.conn, DEFAULT_AGENT_ID, "anonymous-user")
        assert any("面试" in (n.get("content") or "") for n in under_target), (
            "note must be stored under the resolved primary user (ringo)"
        )
        assert not any("面试" in (n.get("content") or "") for n in under_anon), (
            "note must NOT land under anonymous-user — that was the split bug"
        )
        # And the consumer path (resolve_primary_user) reads it back.
        seen = rel.recent_salient_notes(rt.conn, DEFAULT_AGENT_ID, rel.resolve_primary_user(rt.conn, DEFAULT_AGENT_ID))
        assert any("面试" in (n.get("content") or "") for n in seen)
    finally:
        rt.close()
