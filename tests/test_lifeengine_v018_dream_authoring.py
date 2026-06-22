"""v0.18.0 — dreams are AUTHORED from lived life, not the engine's self-check.

Two paths are covered, both offline (no host, no network):
  * **degraded** (no host model): dreams fall back to a clean, life-flavoured
    template — no "LifeEngine / 自检 / 账页" wording, no audit findings woven in,
    and no life_author_runs row (silent degrade).
  * **authored** (a fake host PluginLlm injected): the dream IS the model's
    output, a life_author_runs row records the call, the mood residue lands via
    apply_delta, and the system prompt carries the character + anti-engine
    writing discipline.
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import life_author
from lifeengine.constants import DEFAULT_AGENT_ID, PLUGIN_VERSION
from lifeengine.db import transaction
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.dream import _recent_context
from lifeengine.runtime import LifeEngineRuntime

_BANNED = ["LifeEngine", "自检", "账页", "账本", "资料室", "tick", "trace"]


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 dream authoring test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")  # seed default vitals (energy/mood/fatigue) so the dream's mood residue can land


def _sleep_and_wake(rt: LifeEngineRuntime, *, start="2026-06-10T23:00:00+00:00", wake="2026-06-11T07:00:00+00:00") -> str:
    plan = rt.sleep_tool("plan", planned_sleep_at=start, planned_wake_at=wake, timezone_name="UTC")
    sleep_plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
    rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now=start)
    wake_out = rt.sleep_tool("wake", sleep_plan_id=sleep_plan_id, now=wake)
    return wake_out["receipt"]["facts"][0]["evidence"]["sleep_session_id"]


def _latest_dream_content(rt: LifeEngineRuntime) -> str:
    row = rt.conn.execute(
        "SELECT content FROM dream_entries WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at DESC LIMIT 1",
        (DEFAULT_AGENT_ID,),
    ).fetchone()
    return str(row[0]) if row else ""


class _FakeUsage:
    input_tokens = 140
    output_tokens = 90
    total_tokens = 230
    cost_usd = 0.0012


class _FakeResult:
    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake-provider"
        self.model = "fake-model"


class _FakeLlm:
    """Stands in for agent.plugin_llm.PluginLlm in offline tests."""

    def __init__(self, parsed):
        self._parsed = parsed
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def test_schema_and_life_author_table(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.18.0"
        assert _SCHEMA_VERSION >= 57
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "life_author_runs" in tables
    finally:
        rt.close()


def test_dream_degrades_to_clean_template_without_host(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)  # no host model available
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.memory("remember", content="今天去后山采了点野薄荷，路上遇到隔壁的猫。",
                  memory_type="episodic", source="agent_retro_assertion")
        session_id = _sleep_and_wake(rt)
        dream = rt.dream("run", sleep_session_id=session_id)
        ev = dream["receipt"]["facts"][0]["evidence"]
        assert ev["dream_entry_id"]
        assert ev["truth_layer"] == "dream_symbolic"

        content = _latest_dream_content(rt)
        assert content.strip()
        for word in _BANNED:
            assert word not in content, f"degraded dream must not mention engine word: {word}"

        # silent degrade: no author run recorded when there's no host model
        n = rt.conn.execute(
            "SELECT COUNT(*) FROM life_author_runs WHERE owner_kind='agent' AND owner_id=?",
            (DEFAULT_AGENT_ID,),
        ).fetchone()[0]
        assert n == 0
    finally:
        rt.close()


def test_dream_is_authored_when_host_model_present(tmp_path):
    fresh_home(tmp_path)
    parsed = {
        "content": "梦里我们又站在夜市那家糖画摊前，糖丝在风里被拉得很长很长，灯一盏盏亮起来。",
        "share_text": "我梦见我们在夜市了，醒来有点想你。",
        "symbols": ["糖画", "夜市的灯", "风"],
        "mood_delta": 8,
        "residue": "醒来后特别想再去一次夜市。",
    }
    fake = _FakeLlm(parsed)
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.memory("remember", content="昨晚和朋友逛了夜市，吃了糖画。",
                  memory_type="episodic", source="agent_retro_assertion")
        session_id = _sleep_and_wake(rt)
        dream = rt.dream("run", sleep_session_id=session_id)
        assert dream["receipt"]["facts"][0]["evidence"]["dream_entry_id"]

        # the dream IS the authored content
        content = _latest_dream_content(rt)
        assert "糖画摊" in content
        for word in _BANNED:
            assert word not in content

        # the model was actually called, with a character-derived system prompt
        # that forbids engine/system vocabulary
        assert fake.calls, "host model was not called"
        sys_prompt = fake.calls[0].get("system_prompt") or ""
        assert "绝不提及 LifeEngine" in sys_prompt
        # source material handed to the author is life-domain only — no findings
        passed_text = (fake.calls[0].get("input") or [{}])[0].get("text", "")
        for word in _BANNED:
            assert word not in passed_text

        # the call is recorded for cost/budget/audit
        run = rt.conn.execute(
            "SELECT kind, status, total_tokens FROM life_author_runs WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at DESC LIMIT 1",
            (DEFAULT_AGENT_ID,),
        ).fetchone()
        assert run is not None
        assert run[0] == "dream" and run[1] == "ok"
        assert int(run[2]) == 230

        # the dream leaves a mood residue, applied through apply_delta
        residue = rt.conn.execute(
            "SELECT COUNT(*) FROM resource_ledger WHERE owner_kind='agent' AND owner_id=? AND resource_key='mood' AND reason='梦的余感'",
            (DEFAULT_AGENT_ID,),
        ).fetchone()[0]
        assert residue >= 1

        # doctor stays green on the resource-ledger invariant
        doctor = rt.doctor()
        statuses = {c["name"]: c["status"] for c in doctor["checks"]}
        assert statuses.get("resource_ledger", "ok") not in {"fail", "error"}
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_life_author_does_not_call_host_inside_sqlite_transaction(tmp_path):
    """验证 LifeAuthor 在 SQLite 写事务内只降级，不访问 host 模型。"""
    fresh_home(tmp_path)
    fake = _FakeLlm({
        "content": "这不应该被调用。",
        "share_text": "这不应该被调用。",
        "symbols": [],
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        with transaction(rt.conn):
            parsed = life_author.author(
                rt.conn,
                "agent",
                DEFAULT_AGENT_ID,
                kind="dream",
                instructions="生成一个梦。",
                context={"生活片段": ["院子里晒了被子"]},
                schema={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
            )
        assert parsed is None
        assert fake.calls == []
        n = rt.conn.execute(
            "SELECT COUNT(*) FROM life_author_runs WHERE owner_kind='agent' AND owner_id=?",
            (DEFAULT_AGENT_ID,),
        ).fetchone()[0]
        assert n == 0
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_recent_context_excludes_system_domain_rows(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.memory("remember", content="生活记忆：午后在院子里晒了被子。",
                  memory_type="episodic", source="agent_retro_assertion")
        rt.memory("remember", content="SYSTEM_DEBUG_ROW_should_not_dream",
                  memory_type="system_log", source="agent_retro_assertion")
        ctx = _recent_context(rt.conn, "agent", DEFAULT_AGENT_ID, limit=10)
        contents = " ".join(str(m.get("content") or "") for m in ctx.get("memories") or [])
        assert "晒了被子" in contents
        assert "SYSTEM_DEBUG_ROW_should_not_dream" not in contents
    finally:
        rt.close()
