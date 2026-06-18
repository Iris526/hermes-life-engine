"""Renaming the agent changes only the Canon display name (identity.name) — a
versioned, audited edit that does NOT pause the engine or touch owner_id."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.canon import get_active_canon


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016rn"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")


def _engine_state(rt):
    return rt.conn.execute(
        "SELECT engine_state FROM controls WHERE owner_kind='agent' AND owner_id=?",
        (DEFAULT_AGENT_ID,),
    ).fetchone()["engine_state"]


def test_rename_sets_canon_identity_name(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.rename("明灯")
        assert out["ok"] is True and out["name"] == "明灯"
        canon = get_active_canon(rt.conn, "agent", DEFAULT_AGENT_ID)
        assert (canon.get("identity") or {}).get("name") == "明灯"
        # versioned: a new active Canon version was committed
        active_count = rt.conn.execute(
            "SELECT COUNT(*) c FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active'",
            (DEFAULT_AGENT_ID,),
        ).fetchone()["c"]
        assert active_count == 1
    finally:
        rt.close()


def test_rename_does_not_pause_engine_or_touch_owner_id(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        before = _engine_state(rt)
        rt.rename("明灯")
        after = _engine_state(rt)
        # a rename is cosmetic — it must not pause the running engine
        assert after == before
        assert after != "paused"
        # owner_id is unchanged — the rename only affects the display name
        owners = {r["owner_id"] for r in rt.conn.execute("SELECT DISTINCT owner_id FROM canon_versions WHERE owner_kind='agent'").fetchall()}
        assert owners == {DEFAULT_AGENT_ID}
    finally:
        rt.close()


def test_rename_rejects_blank(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        with pytest.raises(Exception):
            rt.rename("   ")
    finally:
        rt.close()
