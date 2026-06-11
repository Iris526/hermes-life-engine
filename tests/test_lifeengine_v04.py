from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v04"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，货币用日元。天气和我这边一样，heartbeat 手动。")
    rt.commit_canon()
    rt.control("resume")


def test_truth_source_weather_observe_then_resolve_from_cache(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        first = rt.truth("resolve", domain="weather", parameters={"location": "Tokyo"})
        assert first["ok"] is True
        assert first["truth_read"]["status"] in {"requires_observation", "simulated", "resolved"}
        observed = rt.truth(
            "observe",
            domain="weather",
            authority="user_current_location",
            parameters={"location": "Tokyo"},
            result={"condition": "rain", "temperature_c": 22, "truth_layer": "tool_observed"},
            ttl_minutes=120,
        )
        assert observed["truth_read"]["status"] == "observed"
        second = rt.truth("resolve", domain="weather", parameters={"location": "Tokyo"})
        assert second["truth_read"]["status"] == "cached"
        assert second["truth_read"]["result"]["condition"] == "rain"
        listed = rt.truth("list")
        assert "weather" in listed["truth_sources"]["bindings"]
        assert listed["truth_sources"]["cache"]
    finally:
        rt.close()


def test_truth_bind_writes_canon_draft_not_life_event(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        bind = rt.truth("bind", domain="market_price", authority="external_tool", freshness_ttl_minutes=30, fallback="unknown")
        assert bind["ok"] is True
        assert rt.status()["control"]["engine_state"] == "paused_setup"
        committed = rt.commit_canon()["canon"]
        assert committed["data"]["truth_sources"]["bindings"]["market_price"]["authority"] == "external_tool"
        events = rt.event_tool("list")["events"]
        assert not events
    finally:
        rt.close()


def test_heartbeat_records_time_truth_read(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        tick = rt.tick(now="2026-06-07T11:01:00+00:00")
        assert tick["ok"] is True
        assert any(r.get("domain") == "time" for r in tick["truth_refresh"])
        truth = rt.truth("list")
        assert any(r["domain"] == "time" for r in truth["truth_sources"]["recent_reads"])
    finally:
        rt.close()
