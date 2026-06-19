"""v0.18.0 — the autonomy daily goal-step becomes AUTHORED.

The everyday schedule was the last place still emitting the generic
'推进目标：X' template. With a host model, autonomy now asks LifeAuthor for a
concrete, textured next step (a human title + a line of what/why), tying into an
active campaign and the agent's current opinions. Without a host it falls back to
the deterministic template — same as before.
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import life_author
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 autonomy authoring test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


class _U:
    input_tokens = 60; output_tokens = 30; total_tokens = 90; cost_usd = 0.0004


class _R:
    def __init__(self, p): self.parsed = p; self.usage = _U(); self.provider = "f"; self.model = "m"


class _L:
    def __init__(self, p): self._p = p; self.calls = []
    def complete_structured(self, **k): self.calls.append(k); return _R(self._p)


def _count(rt, where, *params):
    return rt.conn.execute(
        f"SELECT COUNT(*) FROM events WHERE owner_kind='agent' AND owner_id=? AND {where}",
        (DEFAULT_AGENT_ID, *params),
    ).fetchone()[0]


def test_autonomy_goal_step_is_authored_with_texture(tmp_path):
    fresh_home(tmp_path)
    fake = _L({"title": "去后山采点安神草药", "description": "昨天听你说最近压力大，想给你带点安神的。"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.goals("create", title="照顾好身边的人", goal_type="relationship", priority=70)
        out = rt.autonomy("run", now="2026-06-12T03:00:00+00:00")
        assert out["ok"]
        # the committed event carries the authored title — not the 推进目标：X template
        assert _count(rt, "title=?", "去后山采点安神草药") == 1
        assert _count(rt, "title LIKE ?", "推进目标%") == 0
        # the goal was handed to the author as context
        assert any("照顾好身边的人" in (c.get("input") or [{}])[0].get("text", "") for c in fake.calls)
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_autonomy_goal_step_falls_back_to_template_without_host(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.goals("create", title="练习日语", goal_type="study", priority=70)
        rt.autonomy("run", now="2026-06-12T03:00:00+00:00")
        # no host → the deterministic template title is used, exactly like before
        assert _count(rt, "title LIKE ?", "推进目标%") >= 1
    finally:
        rt.close()
