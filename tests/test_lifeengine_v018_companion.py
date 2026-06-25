"""v0.18.0 P2 — companion / idle outreach + owner-life relationship memory.

She reaches out when quiet + in a good mood, or to follow up on what you told
her about YOUR life — not only when one of HER events fires. All offline:
relationship memory is plain DB; the outreach line is authored by an injected
fake host model (and simply not produced when there's no host).
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import companion as companion_module
from lifeengine import life_author
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 companion test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


class _FakeUsage:
    input_tokens = 60
    output_tokens = 30
    total_tokens = 90
    cost_usd = 0.0004


class _FakeResult:
    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def _intents_of_type(rt: LifeEngineRuntime, intent_type: str) -> list[tuple]:
    return rt.conn.execute(
        "SELECT id, summary, status FROM proactive_intents WHERE agent_id=? AND intent_type=?",
        (DEFAULT_AGENT_ID, intent_type),
    ).fetchall()


# ---------------------------------------------------------------------------
# relationship memory tool
# ---------------------------------------------------------------------------

def test_relationship_tool_record_list_due(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.relationship("record", content="Ringo 这周四有个面试，挺紧张的。",
                              topic="工作/面试", salience=70, sentiment="concern",
                              follow_up_after_hours=0)
        assert out["ok"] and out["note"]["id"]
        listed = rt.relationship("list")["notes"]
        assert any("面试" in n["content"] for n in listed)
        due = rt.relationship("due")["due"]
        assert any(n["topic"] == "工作/面试" for n in due)
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# companion outreach
# ---------------------------------------------------------------------------

def test_companion_follows_up_on_user_life_when_due(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({"summary": "对了，你周四那个面试后来怎么样啦？", "emotional_tone": "caring"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.relationship("record", content="Ringo 周四有面试，挺紧张。", topic="工作/面试",
                        salience=70, follow_up_after_hours=0)
        out = rt.tick()
        assert out["companion"]["generated"]
        assert out["companion"]["intent_type"] == "ask_about_user"

        asks = _intents_of_type(rt, "ask_about_user")
        assert asks and "面试" in asks[0][1]
        # the note is marked followed-up so she won't keep re-asking
        n = rt.conn.execute(
            "SELECT followed_up_at FROM relationship_notes WHERE agent_id=?", (DEFAULT_AGENT_ID,)
        ).fetchone()
        assert n[0] is not None
        # the author was handed the user's note as context, not an engine self-check
        ctx_text = (fake.calls[0].get("input") or [{}])[0].get("text", "")
        assert "面试" in ctx_text
        assert "LifeEngine" not in ctx_text
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_rejects_report_like_followup_and_keeps_note_due(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({
        "summary": "状态报告：\n1. 已检测到 Ringo 的面试事项。\n2. 建议发起回访。",
        "emotional_tone": "formal",
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.relationship("record", content="Ringo 周四有面试，挺紧张。", topic="工作/面试",
                        salience=70, follow_up_after_hours=0)

        out = rt.tick()

        assert out["companion"]["generated"] is None
        assert _intents_of_type(rt, "ask_about_user") == []
        note = rt.conn.execute(
            "SELECT followed_up_at FROM relationship_notes WHERE agent_id=?", (DEFAULT_AGENT_ID,)
        ).fetchone()
        assert note[0] is None
        audit = rt.conn.execute(
            "SELECT * FROM audit_log WHERE audit_type='companion_author_rejected' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert "multiline" in audit["payload_json"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_idle_share_when_in_good_mood(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({"summary": "刚泡了壶茶，忽然想起你，过得还好吗？", "emotional_tone": "warm"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        # lift the mood well into the "high" band (no due note → unprompted idle
        # share). Several reactions so it clears the band even after the tick's
        # meal-accountability penalties dock some mood.
        for reason in ("今天阳光很好", "收到一条暖心的消息", "顺手把活儿干完了", "傍晚的风很舒服"):
            rt.mood("react", delta=20, reason=reason)
        out = rt.tick()
        assert out["companion"]["generated"]
        assert out["companion"]["intent_type"] == "idle_share"
        idle = _intents_of_type(rt, "idle_share")
        assert idle and idle[0][1]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_idle_share_is_trimmed_to_direct_qq_line(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({"summary": "“我有件事想跟你说：刚泡了壶茶，忽然想起你。”", "emotional_tone": "warm"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        for reason in ("今天阳光很好", "收到一条暖心的消息", "顺手把活儿干完了", "傍晚的风很舒服"):
            rt.mood("react", delta=20, reason=reason)

        out = rt.tick()

        assert out["companion"]["generated"]
        idle = _intents_of_type(rt, "idle_share")
        assert idle and idle[0][1] == "刚泡了壶茶，忽然想起你。"
        assert "我有件事想跟你说" not in idle[0][1]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_silent_without_host_model(tmp_path):
    fresh_home(tmp_path)
    life_author.disable_test_llm()  # no host model -> no canned idle line, just silence
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.relationship("record", content="Ringo 周四有面试。", topic="工作/面试", follow_up_after_hours=0)
        out = rt.tick()
        assert out["companion"]["generated"] is None
        assert _intents_of_type(rt, "ask_about_user") == []
        assert _intents_of_type(rt, "idle_share") == []
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_idle_daily_count_uses_asia_shanghai_boundary(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        created = rt.proactive(
            "create",
            summary="半夜刚过的上海本地日问候。",
            target_type="user",
            target_id="anonymous-user",
            intent_type="idle_share",
            importance=60,
            urgency=40,
            novelty=60,
            relationship_relevance=70,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        rt.conn.execute(
            "UPDATE proactive_intents SET created_at='2026-06-24 16:30:00' WHERE id=?",
            (intent_id,),
        )

        shanghai_count = companion_module._today_idle_count(
            rt.conn,
            DEFAULT_AGENT_ID,
            "anonymous-user",
            timezone_name="Asia/Shanghai",
            now="2026-06-25T15:00:00+08:00",
        )
        utc_count = companion_module._today_idle_count(
            rt.conn,
            DEFAULT_AGENT_ID,
            "anonymous-user",
            timezone_name="UTC",
            now="2026-06-25T07:00:00+00:00",
        )

        assert shanghai_count == 1
        assert utc_count == 0
    finally:
        rt.close()


def test_companion_paces_itself_one_pending_at_a_time(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({"summary": "你周四的面试怎么样啦？", "emotional_tone": "caring"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.relationship("record", content="Ringo 周四面试。", topic="工作/面试", follow_up_after_hours=0)
        rt.tick()
        rt.tick()  # second tick must NOT pile on another idle/companion line
        asks = _intents_of_type(rt, "ask_about_user")
        assert len(asks) == 1
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_rejects_recently_rephrased_idle_prop(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({
        "summary": "师兄，我刚把制符纸摞整齐，发现最上面那张边角翘起来像小猫耳朵，忽然就想给你发一句嘿嘿。",
        "emotional_tone": "cute",
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        created = rt.proactive(
            "create",
            summary="师兄，我刚把摊上的符纸重新压平，发现有一张边角翘起来像小猫耳朵，莫名就想拍给你看。",
            target_type="user",
            target_id="anonymous-user",
            intent_type="idle_share",
            importance=60,
            urgency=40,
            novelty=60,
            relationship_relevance=70,
            privacy_level="safe_to_share",
        )
        old_intent_id = created["results"][0]["result"]["id"]
        rt.conn.execute(
            "UPDATE proactive_intents SET status='suppressed', created_at='2026-06-24 00:00:00' WHERE id=?",
            (old_intent_id,),
        )
        for reason in ("今天阳光很好", "收到一条暖心的消息", "顺手把活儿干完了", "傍晚的风很舒服"):
            rt.mood("react", delta=20, reason=reason)

        out = rt.tick()

        assert out["companion"]["generated"] is None
        idle = _intents_of_type(rt, "idle_share")
        assert len(idle) == 1
        audit = rt.conn.execute(
            "SELECT payload_json FROM audit_log WHERE audit_type='companion_author_rejected' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert "recent_repeat" in audit["payload_json"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


# ---------------------------------------------------------------------------
# owner-life flows into dreams (P2 → P1 hook)
# ---------------------------------------------------------------------------

def test_dream_draws_on_what_the_user_shared(tmp_path):
    fresh_home(tmp_path)
    fake = _FakeLlm({
        "content": "梦里我们在你说的那条河边走，水很亮。",
        "share_text": "梦到我们在河边了。",
        "symbols": ["河", "光"],
        "mood_delta": 5,
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.relationship("record", content="Ringo 说他小时候常去家门口那条河边玩。",
                        topic="童年", salience=80)
        plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00",
                             planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
        sp = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        rt.sleep_tool("start", sleep_plan_id=sp, now="2026-06-10T23:00:00+00:00")
        w = rt.sleep_tool("wake", sleep_plan_id=sp, now="2026-06-11T07:00:00+00:00")
        rt.dream("run", sleep_session_id=w["receipt"]["facts"][0]["evidence"]["sleep_session_id"])
        # the dream author was handed the user's life note as source material
        dream_calls = [c for c in fake.calls if "河边" in (c.get("input") or [{}])[0].get("text", "")]
        assert dream_calls, "the user's shared life did not reach the dream's source material"
    finally:
        life_author.set_test_llm(None)
        rt.close()
