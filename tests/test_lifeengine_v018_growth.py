"""v0.18.0 P4 — reflection → opinions + self-narrative (visible growth).

Lived experience now changes the agent: a daily reflection forms/reinforces
opinions and writes a line of self-narrative, and those resurface in what she
says. All offline: the look-back is authored by an injected fake host model
(and is a no-op without one).
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import life_author, opinions
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 growth test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


class _U:
    input_tokens = 100; output_tokens = 70; total_tokens = 170; cost_usd = 0.001


class _R:
    def __init__(self, p): self.parsed = p; self.usage = _U(); self.provider = "f"; self.model = "m"


class _L:
    def __init__(self, p): self._p = p; self.calls = []
    def complete_structured(self, **k): self.calls.append(k); return _R(self._p)


_REFLECTION = {
    "opinions": [
        {"target": "夜市", "opinion_type": "like", "strength": 0.8, "confidence": 0.7, "reason": "晚上去夜市总让我很开心"},
        {"target": "最近的钱", "opinion_type": "concern", "strength": -0.4, "confidence": 0.6, "reason": "这个月有点紧"},
    ],
    "self_narrative": "这阵子我好像越来越爱往外跑了，也开始操心起钱的事。",
}


def _opinion_row(rt, target):
    return rt.conn.execute(
        "SELECT opinion_type, strength, confidence, evidence_count FROM agent_opinions WHERE agent_id=? AND target=?",
        (DEFAULT_AGENT_ID, target),
    ).fetchone()


def test_schema_and_opinions_table(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "agent_opinions" in tables
    finally:
        rt.close()


def test_reflection_forms_opinions_and_self_narrative(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(_L(_REFLECTION))
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.opinion("reflect", force=True)
        assert out["ok"] and out["opinions"]
        assert _opinion_row(rt, "夜市")[0] == "like"
        assert _opinion_row(rt, "最近的钱")[0] == "concern"
        # the self-narrative is stored and readable — she can voice it
        nar = rt.opinion("narrative")["self_narrative"]
        assert "往外跑" in nar
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_repeated_reflection_reinforces_not_duplicates(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(_L(_REFLECTION))
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.opinion("reflect", force=True)
        before = _opinion_row(rt, "夜市")
        rt.opinion("reflect", force=True)
        after = _opinion_row(rt, "夜市")
        # one row per (target, type); evidence + confidence grow
        rows = rt.conn.execute("SELECT COUNT(*) FROM agent_opinions WHERE agent_id=? AND target='夜市'", (DEFAULT_AGENT_ID,)).fetchone()[0]
        assert rows == 1
        assert after[3] == before[3] + 1          # evidence_count +1
        assert after[2] >= before[2]              # confidence non-decreasing
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_reflection_runs_once_a_day_on_the_heartbeat(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(_L(_REFLECTION))
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out1 = rt.tick()
        assert out1["reflection"].get("opinions")          # first tick reflected
        n1 = rt.conn.execute("SELECT COUNT(*) FROM agent_opinions WHERE agent_id=?", (DEFAULT_AGENT_ID,)).fetchone()[0]
        out2 = rt.tick()
        assert out2["reflection"].get("skipped")           # second tick same day: paced out
        n2 = rt.conn.execute("SELECT COUNT(*) FROM agent_opinions WHERE agent_id=?", (DEFAULT_AGENT_ID,)).fetchone()[0]
        assert n1 == n2
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_partial_reasons_include_v018_sections(tmp_path):
    """验证 v0.18 新增 heartbeat 子流程失败会进入 partial 原因。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        reasons = rt._heartbeat_partial_reasons({
            "reflection": {"status": "error"},
            "campaigns": {"ok": False},
            "companion": {"error": "author failed"},
        })
        assert "reflection:error" in reasons
        assert "campaigns:ok_false" in reasons
        assert "companion:error" in reasons
    finally:
        rt.close()


def test_reflection_is_a_noop_without_host_model(tmp_path):
    fresh_home(tmp_path)
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.opinion("reflect", force=True)
        assert out.get("degraded") is True
        assert rt.opinion("list")["opinions"] == []
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_opinion_tool_record_and_read(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.opinion("record", target="记账这件事", opinion_type="value", strength=0.6, reason="让我安心")
        listed = rt.opinion("list")["opinions"]
        assert any(o["target"] == "记账这件事" and o["opinion_type"] == "value" for o in listed)
    finally:
        rt.close()


def test_opinions_resurface_in_idle_companion_share(tmp_path):
    fresh_home(tmp_path)
    fake = _L({"summary": "刚路过夜市，又想起你了。", "emotional_tone": "warm"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.opinion("record", target="夜市", opinion_type="like", strength=0.8, reason="晚上去总很开心")
        for reason in ("天气很好", "活儿干完了", "喝到好茶", "傍晚的风很舒服"):
            rt.mood("react", delta=20, reason=reason)
        rt.tick()
        idle = [c for c in fake.calls if c.get("purpose") == "life_author:idle_share"]
        assert idle, "companion idle share was not authored"
        assert "夜市" in (idle[0].get("input") or [{}])[0].get("text", "")
    finally:
        life_author.set_test_llm(None)
        rt.close()
