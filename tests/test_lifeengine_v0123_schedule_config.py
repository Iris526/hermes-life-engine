import tempfile

import pytest

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life
from lifeengine.constants import PLUGIN_VERSION


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v0123_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_version_v0123():
    assert PLUGIN_VERSION == "0.12.4"


def test_schedule_semantics_explain_and_unscheduled_event():
    rt = LifeEngineRuntime()
    try:
        e = rt.event_tool("create", title="测试计划但不排期的事项", event_type="work", event_category="work", status="planned")
        assert e["ok"] is True
        out = rt.schedule("unscheduled")
        assert "计划中但尚未排期" in out["rendered"]
        assert "测试计划但不排期" in out["rendered"]
        explain = rt.schedule("explain")
        assert "Event 是“事情”" in explain["rendered"]
        assert "ScheduleBlock 是“时间块”" in explain["rendered"]
    finally:
        rt.close()


def test_config_summary_and_draft_patch_human_friendly():
    rt = LifeEngineRuntime()
    try:
        summary = rt.required_settings("summary")
        assert "LifeEngine 设定摘要" in summary["rendered"]
        patched = rt.required_settings("patch", path="truth_sources.bindings.weather.authority", value="narrative_simulator")
        assert patched["ok"] is True
        assert "设定草案" in patched["rendered"]
        draft = rt.required_settings("draft")
        assert "truth_sources" in draft["rendered"] or "已记录的设定块" in draft["rendered"]
    finally:
        rt.close()


def test_slash_schedule_and_config_new_surfaces():
    assert "Event 是“事情”" in slash_life("schedule explain")
    assert "计划中但尚未排期" in slash_life("schedule unscheduled")
    assert "LifeEngine 设定摘要" in slash_life("config")
    assert "设定草案" in slash_life("config set truth_sources.bindings.weather.authority narrative_simulator")
