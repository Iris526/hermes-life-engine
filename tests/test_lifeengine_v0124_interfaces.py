import tempfile
import pytest

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.runtime import LifeEngineRuntime


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v0124_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_v0124_version_and_interface_catalog():
    assert PLUGIN_VERSION == "0.12.8"
    rt = LifeEngineRuntime()
    try:
        out = rt.interface("catalog")
        assert out["ok"] is True
        assert "schedule" in out["domains"]
        assert "LifeEngine 接口目录" in out["rendered"]
    finally:
        rt.close()


def test_life_interface_config_defaults_write_to_draft_only():
    rt = LifeEngineRuntime()
    try:
        before = rt.required_settings("summary")
        out = rt.interface("write", domain="config", intent="apply_default_draft", kind="virtual_random")
        assert out["ok"] is True
        assert out["draft"]["extracted"]["truth_sources"]["bindings"]["weather"]["authority"] == "narrative_simulator"
        after = rt.required_settings("summary")
        # Active Canon remains unchanged until commit.
        assert before["canon"] == after["canon"]
    finally:
        rt.close()


def test_life_interface_schedule_event_and_unscheduled():
    rt = LifeEngineRuntime()
    try:
        ev = rt.event_tool("create", title="接口测试事件", event_type="work", event_category="work")
        event_id = ev["receipt"]["facts"][0]["evidence"]["event_id"]
        uns = rt.interface("read", domain="schedule", view="unscheduled")
        assert event_id in {x["id"] for x in uns["items"]}
        out = rt.interface("write", domain="schedule", intent="schedule_event", event_id=event_id, start="2030-01-01T10:00:00+09:00", end="2030-01-01T11:00:00+09:00", timezone="Asia/Tokyo")
        assert out["ok"] is True
        assert out["receipt"]["facts"]
        day = rt.interface("read", domain="schedule", view="day", date="2030-01-01")
        assert any(x.get("event_id") == event_id for x in day["items"])
    finally:
        rt.close()
