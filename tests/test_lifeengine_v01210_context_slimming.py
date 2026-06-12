import pytest
pytest.importorskip("sqlite_vec")
from lifeengine.db import connect, _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import PLUGIN_VERSION


def test_v01210_schema_and_context_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.10"
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
        rt.setup("v0.12.10 context micro test agent")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t2", "帮我看看衣柜和穿搭")
        assert len(ctx) <= 2600 + 200  # closing truncation marker allowance
        assert "life_collection" in ctx or "collection" in ctx
    finally:
        rt.close()
