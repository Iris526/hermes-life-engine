"""v0.18.0 — WebUI surfaces the new mechanics (campaigns / inner-life / relationship).

The observatory HUD now reads v0.18 state so the human can SEE the arc she's in
the middle of, her self-narrative + opinions, and what she remembers about you.
Seed real data via the runtime, then check the reader/snapshot + the new
endpoints expose it.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from lifeengine import life_author
from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


class _L:
    def __init__(self, p):
        self._p = p
        self.calls = []
    def complete_structured(self, **k):
        self.calls.append(k)
        return type("R", (), {"parsed": self._p, "usage": type("U", (), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cost_usd": 0.0})(), "provider": "f", "model": "m"})()


def _seed(tmp_path: Path) -> str:
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    rt = LifeEngineRuntime()
    try:
        rt.setup("v0.18 webui test agent")
        rt.commit_canon()
        rt.control("resume")
        rt.living("init_resources")
        life_author.set_test_llm(_L({
            "opinions": [{"target": "夜市", "opinion_type": "like", "strength": 0.8, "confidence": 0.7, "reason": "晚上去总很开心"}],
            "self_narrative": "这阵子我好像越来越爱往外跑了。",
        }))
        rt.opinion("reflect", force=True)
        life_author.set_test_llm(None)
        rt.campaign("register", title="夏夜庙会筹备",
                    phases=[{"title": "筹备", "duration_days": 3, "daily_spawns": 1, "spawn_template": {"title": "采买灯笼"}},
                            {"title": "庙会当夜", "duration_days": 1, "daily_spawns": 0}],
                    timezone="UTC", start_date="2026-06-12")
        rt.relationship("record", content="Ringo 周四有个面试。", topic="工作/面试", follow_up_after_hours=0)
        return str(db_path())
    finally:
        rt.close()


def test_reader_snapshot_includes_v018_sections(tmp_path):
    db = _seed(tmp_path)
    reader = LifeEngineReader(db)
    snap = reader.snapshot("agent", "default-agent")

    camps = snap["campaigns"]
    assert any(c["title"] == "夏夜庙会筹备" and c["status"] == "active" for c in camps)
    assert camps[0]["phase_count"] == 2 and camps[0]["current_phase_title"] == "筹备"

    inner = snap["inner_life"]
    assert "往外跑" in (inner["self_narrative"] or {}).get("content", "")
    assert any(o["target"] == "夜市" and o["opinion_type"] == "like" for o in inner["opinions"])

    assert any("面试" in n["content"] for n in snap["relationship"])


def test_new_endpoints_and_version(tmp_path):
    db = _seed(tmp_path)
    client = TestClient(create_app(db))
    assert client.get("/api/health").json()["webui_version"] == "0.18.0"
    assert any(c["title"] == "夏夜庙会筹备" for c in client.get("/api/campaigns").json()["items"])
    assert "往外跑" in (client.get("/api/inner_life").json()["self_narrative"] or {}).get("content", "")
    assert any("面试" in n["content"] for n in client.get("/api/relationship").json()["items"])
