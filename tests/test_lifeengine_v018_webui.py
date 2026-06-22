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


def _result(commit: dict, index: int = 0) -> dict:
    return ((commit.get("results") or [])[index].get("result") or {})


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
        rt.social("define_slot", slot_type="entity_kind", key="club", label="社团")
        rt.social("define_slot", slot_type="reputation_axis", key="craft_credit", label="手作信用")
        agent = _result(rt.social("create_entity", entity_kind="person", display_name="明灯", summary="当前生活主体"))
        club = _result(rt.social("create_entity", entity_kind="club", display_name="手作社", summary="重视作品交付的小社团"))
        elder = _result(rt.social("create_entity", entity_kind="person", display_name="社团前辈"))
        rt.social("link_affiliation", subject_entity_id=agent["id"], faction_entity_id=club["id"], role="member", strength=0.8)
        rt.social("set_edge", source_entity_id=elder["id"], target_entity_id=agent["id"], axis="trust", value=35, confidence=0.7)
        rt.social("reputation_event", subject_entity_id=agent["id"], audience_entity_id=club["id"],
                  axis="craft_credit", delta=18, reason="按时交付社团委托")
        rt.social("evaluate", evaluator_entity_id=club["id"], subject_entity_id=agent["id"],
                  axis="reliability", score=22, reason="交付稳定")
        rt.social("rumor", subject_entity_id=agent["id"], content="有人说她最近变得很可靠。",
                  channel="club_chat", heat=0.7, credibility=0.4)
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

    social = snap["social_world"]
    assert any(s["slot_type"] == "entity_kind" and s["key"] == "club" for s in social["slots"])
    assert any(e["display_name"] == "手作社" and e["entity_kind"] == "club" for e in social["entities"])
    assert any(a["subject_name"] == "明灯" and a["faction_name"] == "手作社" for a in social["affiliations"])
    assert any(r["axis"] == "craft_credit" and r["subject_name"] == "明灯" for r in social["reputation"])
    assert any("可靠" in r["content"] and r["truth_layer"] == "rumor_unverified" for r in social["rumors"])


def test_new_endpoints_and_version(tmp_path):
    db = _seed(tmp_path)
    client = TestClient(create_app(db))
    assert client.get("/api/health").json()["webui_version"] == "0.18.0"
    assert any(c["title"] == "夏夜庙会筹备" for c in client.get("/api/campaigns").json()["items"])
    assert "往外跑" in (client.get("/api/inner_life").json()["self_narrative"] or {}).get("content", "")
    assert any("面试" in n["content"] for n in client.get("/api/relationship").json()["items"])
    social = client.get("/api/social_world").json()
    assert any(e["display_name"] == "明灯" for e in social["entities"])
    assert any(r["axis"] == "craft_credit" for r in social["reputation"])
