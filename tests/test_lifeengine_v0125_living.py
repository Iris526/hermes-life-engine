import os


def test_living_consistency_and_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        assert rt.living("consistency")["ok"] is True
        out = rt.living("init_inventory")
        assert out["ok"] is True
        assert "符纸" in out["rendered"]
        assert "归明观" not in out["rendered"]
        resources = rt.resources("list")
        keys = {r["key"] for r in resources["resources"]["definitions"]}
        assert "money.lingzhu" in keys
        items = rt.inventory("list")["items"]
        assert any(i["name"] == "符纸" for i in items)
    finally:
        rt.close()


def test_living_day_rhythm_creates_concrete_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        out = rt.living("day_rhythm", date="2030-01-02")
        assert out["ok"] is True
        assert out["event_ids"]
        assert out["schedule_block_ids"]
        sched = rt.schedule("day", date="2030-01-02")
        assert "小道观晨巡" in sched["rendered"]
        assert "归明观晨巡" not in sched["rendered"]
        assert "已排期" in sched["rendered"]
    finally:
        rt.close()


def test_living_paper_note_and_interface(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        out = rt.interface("write", domain="living", intent="create_note", summary="今天接了一个小委托，想之后告诉 重要的人。")
        assert out["ok"] is True
        notes = rt.interface("read", domain="living", view="paper_notes")
        assert "小委托" in notes["rendered"]
    finally:
        rt.close()


def test_living_decompose_missing_event_returns_structured_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        out = rt.living("decompose_abstract", event_id="missing-event")
        assert out["ok"] is False
        assert out["error"] == "abstract event not found"
    finally:
        rt.close()


def test_living_temple_life_preset_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        out = rt.living("day_rhythm", date="2030-01-03", preset="temple_life")
        assert out["ok"] is True
        sched = rt.schedule("day", date="2030-01-03")
        assert "小道观晨巡" in sched["rendered"]
    finally:
        rt.close()
