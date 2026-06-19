"""v0.18.0 — the inner life surfaces in the per-turn context.

The capstone: everything v0.18 tracks (self-narrative, opinions, the arc she's in
the middle of, the thing she meant to ask you) is injected into the context the
host model sees each turn — so 明灯 can actually voice her growth, her project,
and her care for your life in conversation, instead of it just sitting in tables.
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import life_author
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 inner-life context test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


class _U:
    input_tokens = 80; output_tokens = 50; total_tokens = 130; cost_usd = 0.0008


class _R:
    def __init__(self, p): self.parsed = p; self.usage = _U(); self.provider = "f"; self.model = "m"


class _L:
    def __init__(self, p): self._p = p; self.calls = []
    def complete_structured(self, **k): self.calls.append(k); return _R(self._p)


def test_inner_life_surfaces_in_turn_context(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(_L({
        "opinions": [{"target": "夜市", "opinion_type": "like", "strength": 0.8, "confidence": 0.7, "reason": "晚上去总很开心"}],
        "self_narrative": "这阵子我好像越来越爱往外跑了。",
    }))
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.context("set", budget_chars=20000)  # generous budget so nothing trims
        rt.opinion("reflect", force=True)        # → an opinion + a self-narrative
        rt.campaign("register", title="夏夜庙会筹备",
                    phases=[{"title": "筹备", "duration_days": 3, "daily_spawns": 1,
                             "spawn_template": {"title": "采买灯笼"}}],
                    timezone="UTC", start_date="2026-06-12")
        rt.relationship("record", content="Ringo 周四有个面试。", topic="工作/面试", follow_up_after_hours=0)

        life_author.set_test_llm(None)  # building context reads only — no model needed
        ctx = rt.build_context_for_turn("s1", "t1", "你最近怎么样？")

        assert "inner_life" in ctx
        assert "往外跑" in ctx          # her self-narrative
        assert "夜市" in ctx            # an opinion she holds
        assert "夏夜庙会筹备" in ctx     # the arc she's in the middle of
        assert "面试" in ctx            # what she meant to ask you about
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_inner_life_empty_when_nothing_formed(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        # fresh agent: no opinions / campaign / due notes → inner_life stays empty,
        # context still builds fine
        cap = rt._inner_life_capsule("agent", "default-agent")
        assert cap == {}
        ctx = rt.build_context_for_turn("s1", "t1", "在吗")
        assert "LIFEENGINE_CONTEXT" in ctx
    finally:
        rt.close()
