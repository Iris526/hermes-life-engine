import os


def test_living_consistency_and_resource_preset(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        assert rt.living("consistency")["ok"] is True
        out = rt.living("init_resources")
        assert out["ok"] is True
        assert "money.lingzhu" in out["rendered"]
        resources = rt.resources("list")
        keys = {r["key"] for r in resources["resources"]["definitions"]}
        assert "money.lingzhu" in keys
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
        assert "归明观晨巡" in sched["rendered"]
        assert "已排期" in sched["rendered"]
    finally:
        rt.close()


def test_heartbeat_generates_daily_rhythm_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，heartbeat 要能自己补齐当天生活节奏。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="autonomy", value="off")
        rt.control("module", key="managed_review_loop", value="off")
        rt.living("init_resources")

        first = rt.tick(now="2030-01-02T00:00:00+00:00")
        second = rt.tick(now="2030-01-02T01:00:00+00:00")

        assert first["daily_rhythm"]["status"] == "ok"
        assert first["daily_rhythm"]["event_ids"]
        assert first["daily_rhythm"]["schedule_block_ids"]
        assert second["daily_rhythm"]["status"] == "skipped"
        assert second["daily_rhythm"]["reason"] == "already generated today"
        run_count = rt.conn.execute(
            "SELECT COUNT(*) FROM life_rhythm_runs WHERE action='heartbeat_daily_rhythm' AND date_key='2030-01-02'",
        ).fetchone()[0]
        assert run_count == 1
    finally:
        rt.close()


def test_living_paper_note_and_interface(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from lifeengine.runtime import LifeEngineRuntime
    rt = LifeEngineRuntime()
    try:
        out = rt.interface("write", domain="living", intent="create_note", summary="今天接了一个小委托，想之后告诉 Ringo。")
        assert out["ok"] is True
        notes = rt.interface("read", domain="living", view="paper_notes")
        assert "小委托" in notes["rendered"]
    finally:
        rt.close()
