from __future__ import annotations

import os
import shutil

import pytest

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def test_agent_setup_commit_resume_and_hash_verify(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert rt.status()["control"]["engine_state"] == "setup_required"
        draft = rt.setup("你和我同城，货币用日元。你有一个资源叫灵感值，范围 0 到 100，初始 50。")["draft"]
        assert draft["statement_count"] == 1
        committed = rt.commit_canon()["canon"]
        assert committed["version"] == 1
        assert "migration" in committed
        assert rt.status()["control"]["engine_state"] == "active"
        rt.control("resume")
        assert rt.traces("verify")["ok"] is True
    finally:
        rt.close()


def test_user_life_rejects_agent_narrative_source(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        rt.setup("user life", "user", "u1")
        rt.commit_canon("user", "u1")
        rt.control("resume", "user", "u1")
        with pytest.raises(Exception):
            rt.commit_ops(
                [{"type": "CREATE_EVENT", "payload": {"title": "用户午饭", "source": "agent_retro_assertion"}}],
                "user", "u1", source="agent_retro_assertion",
            )
        ok = rt.commit_ops(
            [{"type": "CREATE_EVENT", "payload": {"title": "用户午饭", "source": "user_reported"}}],
            "user", "u1", source="user_reported",
        )
        assert ok["ok"] is True
    finally:
        rt.close()


def test_resource_reservation_cannot_exceed_available(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        rt.setup("agent", "agent", "default-agent")
        rt.commit_canon("agent", "default-agent")
        rt.resources("define", key="energy", display_name="Energy", initial=10)
        with pytest.raises(Exception):
            rt.commit_ops([{"type": "RESOURCE_RESERVE", "payload": {"resource_key": "energy", "amount": 20}}])
    finally:
        rt.close()
