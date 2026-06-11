import os
import tempfile

import pytest

from lifeengine.constants import PLUGIN_VERSION, DEFAULT_MODULE_GATES
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime, format_result
from lifeengine.cli import slash_life


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01119_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_version_schema_and_defaults_are_agent_managed():
    assert PLUGIN_VERSION == "0.12.4"
    assert _SCHEMA_VERSION == 39
    assert DEFAULT_MODULE_GATES["autonomy"] == "full"
    assert DEFAULT_MODULE_GATES["managed_review_loop"] == "auto"


def test_schedule_is_human_readable_default_today_and_week():
    rt = LifeEngineRuntime()
    try:
        out = rt.schedule("today")
        assert out["ok"] is True
        assert "rendered" in out
        assert "今天的日程" in out["rendered"]
        week = rt.schedule("week")
        assert "这一周的日程" in week["rendered"]
    finally:
        rt.close()


def test_required_settings_are_human_readable_and_startup_persists_check():
    rt = LifeEngineRuntime()
    try:
        out = rt.startup_check()
        assert out["ok"] is True
        check = rt.required_settings("latest")
        assert "LifeEngine 必选设定检查" in check["rendered"]
        assert "人设" in check["rendered"]
    finally:
        rt.close()


def test_review_is_human_readable_and_not_internal_gate_json():
    rt = LifeEngineRuntime()
    try:
        rendered = format_result(rt.review("summary"))
        assert rendered.startswith("LifeEngine Review")
        assert "待处理 / 建议" in rendered or "没有需要人类处理" in rendered
        assert "unsupported_json" not in rendered
    finally:
        rt.close()


def test_slash_surface_has_schedule_and_config():
    assert "/life schedule" in slash_life("help")
    assert "今天的日程" in slash_life("schedule")
    assert "这一周的日程" in slash_life("schedule week")
    assert "LifeEngine 必选设定检查" in slash_life("config")
