import pytest
pytest.importorskip("sqlite_vec")
from lifeengine.db import connect, _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import PLUGIN_VERSION


def test_v01210_schema_and_context_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.13.0"
        assert _SCHEMA_VERSION == 45
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] == 45
        row = rt.conn.execute("SELECT name FROM sqlite_master WHERE name='prompt_context_runs'").fetchone()
        assert row is not None
        out = rt.context("policy")
        assert out["ok"] is True
        assert "上下文策略" in out["rendered"]
    finally:
        rt.close()


def test_context_is_slim_and_records_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rt = LifeEngineRuntime()
    try:
        rt.setup("你叫明灯，生活在归明观。时间使用 Asia/Shanghai，天气使用叙事模拟器。")
        rt.commit_canon()
        ctx = rt.build_context_for_turn("s1", "t1", "今天日程是什么？")
        assert "LIFEENGINE_CONTEXT" in ctx
        assert "progressive_slim" in ctx
        assert len(ctx) < 9000
        runs = rt.context("runs")["runs"]
        assert runs
        assert runs[0]["output_chars"] == len(ctx)
    finally:
        rt.close()


def test_context_mode_micro(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rt = LifeEngineRuntime()
    try:
        rt.context("set", mode="micro", budget_chars=2400)
        rt.setup("v0.13.0 context micro test agent")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t2", "帮我看看衣柜和穿搭")
        assert len(ctx) <= 2600 + 200  # closing truncation marker allowance
        assert "life_collection" in ctx or "collection" in ctx
    finally:
        rt.close()


def test_pre_llm_hook_does_not_mount_lifeengine_on_cli_without_session_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.hooks import pre_llm_call

    out = pre_llm_call(session_id="cli_s1", turn_id="cli_t1", user_message="普通工作问题", platform="cli")

    assert out is None


def test_pre_llm_hook_mounts_lifeengine_on_qq_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.hooks import pre_llm_call

    out = pre_llm_call(session_id="qq_s1", turn_id="qq_t1", user_message="今天在干嘛", platform="qqbot")

    assert out is not None
    assert "LIFEENGINE" in out["context"]


def test_pre_llm_hook_mount_requires_explicit_session_switch_on_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.hooks import pre_llm_call

    rt = LifeEngineRuntime()
    try:
        mounted = rt.context("mount", session_id="cli_s2", platform="cli")
        assert mounted["mounted"] is True
        out = pre_llm_call(session_id="cli_s2", turn_id="cli_t2", user_message="普通工作问题", platform="cli")
        assert out is not None
        assert "LIFEENGINE" in out["context"]

        unmounted = rt.context("unmount", session_id="cli_s2", platform="cli")
        assert unmounted["mounted"] is False
        out2 = pre_llm_call(session_id="cli_s2", turn_id="cli_t3", user_message="普通工作问题", platform="cli")
        assert out2 is None
    finally:
        rt.close()


def test_pre_llm_hook_does_not_mount_from_life_like_message_on_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.hooks import pre_llm_call

    out = pre_llm_call(session_id="cli_s3", turn_id="cli_t3", user_message="/life status", platform="cli")

    assert out is None


def test_slash_life_context_mount_uses_gateway_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.cli import slash_life
    from lifeengine.hooks import pre_llm_call

    rendered = slash_life("context mount", session_id="slash_s1", turn_id="slash_t1", platform="cli")
    assert "mounted" in rendered

    out = pre_llm_call(session_id="slash_s1", turn_id="slash_t2", user_message="普通工作问题", platform="cli")
    assert out is not None
    assert "LIFEENGINE" in out["context"]
