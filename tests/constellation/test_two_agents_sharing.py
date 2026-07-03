"""轴五-B generation face：Agent 把公开讲述分享给 active peer agents。"""

from __future__ import annotations

import json
from pathlib import Path

from lifeengine.canon import ensure_control
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.inter_agent import run_inter_agent_sharing_for_tick
from lifeengine.jsonutil import loads
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.trace import new_id


PUBLIC_TELLING = "今天把小摊的账本重新整理了一遍，终于松了口气。"
PRIVATE_THOUGHT = "PRIVATE_THOUGHT_SHOULD_NOT_LEAK"
PRIVATE_MEMORY = "PRIVATE_MEMORY_SHOULD_NOT_LEAK"
PRIVATE_INTENT = "PRIVATE_INTENT_SHOULD_NOT_LEAK"


def _fresh_home(tmp_path: Path, monkeypatch) -> Path:
    """为 sharing 测试创建隔离 HERMES_HOME。

    输入是 pytest tmp_path/monkeypatch；输出是测试专属 home。调用方随后创建两个
    active agents。函数只准备目录和环境变量，不读写 LifeEngine 业务表。
    """
    home = tmp_path / "hermes_home_constellation_sharing"
    monkeypatch.setenv("HERMES_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    return home


def _setup_two_agents(rt: LifeEngineRuntime) -> str:
    """创建默认 Agent A 和 active peer Agent B。

    输入是 runtime；输出是 B 的 owner_id。调用方用于 sharing/channel 测试。副作用
    是通过 setup/commit 初始化两个 agent 的 Canon/control，保持 owner 参数化。
    """
    agent_b = "agent-lin"
    rt.setup("名字是 明灯。她经营一个小摊，常把生活里的小进展讲给熟人。", "agent", DEFAULT_AGENT_ID)
    rt.commit_canon("agent", DEFAULT_AGENT_ID)
    rt.setup("名字是 凛，是现代插画师。", "agent", agent_b)
    rt.commit_canon("agent", agent_b)
    return agent_b


def _create_shareable_and_private_sources(rt: LifeEngineRuntime, agent_b: str) -> str:
    """给 A 写入一个公开 idle_share 和多个私有干扰项。

    输入是 runtime 与 peer id；输出公开 proactive intent id。公开项是
    `proactive_intents.intent_type='idle_share'` 且 privacy_level=safe_to_share；
    私有项覆盖 thoughts、memories、agent_private proactive intent。调用方用这些
    token 断言 sharing runner 只读公开 surface，不读私有存储。
    """
    public = rt.proactive(
        "create",
        owner_id=DEFAULT_AGENT_ID,
        target_type="user",
        target_id="u1",
        intent_type="idle_share",
        summary=PUBLIC_TELLING,
        privacy_level="safe_to_share",
        importance=90,
        urgency=80,
        novelty=80,
        relationship_relevance=80,
    )
    public_intent_id = public["results"][0]["result"]["id"]
    rt.conn.execute(
        """INSERT INTO thoughts(id, owner_kind, owner_id, thought_type, content, status)
           VALUES(?,?,?,?,?,?)""",
        (new_id("thought"), "agent", DEFAULT_AGENT_ID, "private_test", PRIVATE_THOUGHT, "private"),
    )
    rt.conn.execute(
        """INSERT INTO memories(id, owner_kind, owner_id, memory_type, content, source)
           VALUES(?,?,?,?,?,?)""",
        (new_id("mem"), "agent", DEFAULT_AGENT_ID, "private_test", PRIVATE_MEMORY, "test"),
    )
    rt.proactive(
        "create",
        owner_id=DEFAULT_AGENT_ID,
        target_type="agent_private",
        target_id=agent_b,
        intent_type="idle_share",
        summary=PRIVATE_INTENT,
        privacy_level="agent_private",
        importance=100,
        urgency=100,
        novelty=100,
        relationship_relevance=100,
    )
    return public_intent_id


def _b_world_text(rt: LifeEngineRuntime, agent_b: str) -> str:
    """收集 B 世界中可能承载跨 Agent 内容的文本。

    输入是 runtime 与 B owner_id；输出 JSON 文本。调用方只用于测试断言，覆盖
    rumors、peer entities、memories 和 diary entries，确认私有 token 没有落入 B。
    """
    rumors = rt.conn.execute(
        "SELECT content, evidence_json, truth_layer FROM rumors WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (agent_b,),
    ).fetchall()
    entities = rt.conn.execute(
        "SELECT display_name, summary, metadata_json FROM world_entities WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (agent_b,),
    ).fetchall()
    memories = rt.conn.execute(
        "SELECT memory_type, content FROM memories WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (agent_b,),
    ).fetchall()
    diaries = rt.conn.execute(
        "SELECT diary_type, content, privacy FROM diary_entries WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (agent_b,),
    ).fetchall()
    return json.dumps(
        {
            "rumors": [dict(row) for row in rumors],
            "entities": [dict(row) for row in entities],
            "memories": [dict(row) for row in memories],
            "diaries": [dict(row) for row in diaries],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def test_two_agents_share_public_idle_telling_with_privacy_gate_and_idempotency(monkeypatch, tmp_path: Path) -> None:
    """A 的公开 idle_share 只在 gate 开启时分享，并在 B 世界保持 rumor_unverified。

    覆盖 gate off no-op、truth-layer、peer_agent、隐私不泄漏和 dedup 幂等。sharing
    runner 被直接调用，等价于 heartbeat wrapper 的核心业务路径，但避开无关模块噪声。
    """
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        agent_b = _setup_two_agents(rt)
        public_intent_id = _create_shareable_and_private_sources(rt, agent_b)

        gate_off = run_inter_agent_sharing_for_tick(
            rt.conn,
            ("agent", DEFAULT_AGENT_ID),
            control=ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID),
        )
        assert gate_off is None
        assert rt.conn.execute("SELECT COUNT(*) FROM inter_agent_outbox").fetchone()[0] == 0
        assert rt.conn.execute(
            "SELECT COUNT(*) FROM rumors WHERE owner_kind='agent' AND owner_id=?",
            (agent_b,),
        ).fetchone()[0] == 0

        rt.control("module", owner_id=DEFAULT_AGENT_ID, key="inter_agent", value="on")
        shared = run_inter_agent_sharing_for_tick(
            rt.conn,
            ("agent", DEFAULT_AGENT_ID),
            control=ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID),
        )
        assert shared is not None
        assert shared["ok"] is True
        assert shared["enqueued_count"] == 1
        assert shared["delivered_count"] == 1

        outbox_rows = rt.conn.execute(
            "SELECT * FROM inter_agent_outbox ORDER BY created_at"
        ).fetchall()
        assert len(outbox_rows) == 1
        outbox = dict(outbox_rows[0])
        assert outbox["from_owner_id"] == DEFAULT_AGENT_ID
        assert outbox["to_owner_id"] == agent_b
        assert outbox["kind"] == "telling"
        assert outbox["truth_layer"] == "rumor_unverified"
        assert outbox["status"] == "delivered"
        assert outbox["dedup_key"] == f"share:proactive:{public_intent_id}:agent:{agent_b}"

        rumors = rt.conn.execute(
            """SELECT * FROM rumors
               WHERE owner_kind='agent' AND owner_id=? AND target_kind='inter_agent_outbox'
               ORDER BY created_at""",
            (agent_b,),
        ).fetchall()
        assert len(rumors) == 1
        rumor = dict(rumors[0])
        assert rumor["truth_layer"] == "rumor_unverified"
        assert PUBLIC_TELLING in rumor["content"]
        evidence = loads(rumor["evidence_json"], {})
        assert evidence["source"] == "inter_agent_outbox"
        assert evidence["from_owner_id"] == DEFAULT_AGENT_ID
        assert evidence["dedup_key"] == outbox["dedup_key"]
        assert (evidence["payload"] or {})["source_id"] == public_intent_id

        peers = rt.conn.execute(
            """SELECT * FROM world_entities
               WHERE owner_kind='agent' AND owner_id=? AND entity_kind='peer_agent' AND status='active'""",
            (agent_b,),
        ).fetchall()
        assert len(peers) == 1
        peer_meta = loads(peers[0]["metadata_json"], {})
        assert peer_meta["peer_owner_id"] == DEFAULT_AGENT_ID

        b_text = _b_world_text(rt, agent_b)
        assert PUBLIC_TELLING in b_text
        assert PRIVATE_THOUGHT not in b_text
        assert PRIVATE_MEMORY not in b_text
        assert PRIVATE_INTENT not in b_text
        assert PUBLIC_TELLING not in (rt.opinion("narrative", owner_id=agent_b)["self_narrative"] or "")

        shared_again = run_inter_agent_sharing_for_tick(
            rt.conn,
            ("agent", DEFAULT_AGENT_ID),
            control=ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID),
        )
        assert shared_again is not None
        assert shared_again["enqueued_count"] == 0
        assert rt.conn.execute("SELECT COUNT(*) FROM inter_agent_outbox").fetchone()[0] == 1
        assert rt.conn.execute(
            "SELECT COUNT(*) FROM rumors WHERE owner_kind='agent' AND owner_id=? AND target_kind='inter_agent_outbox'",
            (agent_b,),
        ).fetchone()[0] == 1
    finally:
        rt.close()


def test_inter_agent_sharing_noops_when_gate_on_but_no_peer(monkeypatch, tmp_path: Path) -> None:
    """只有一个 active agent 时，即使 gate 开启也不产生 outbox。"""
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        rt.setup("名字是 明灯。她有时会讲近况。", "agent", DEFAULT_AGENT_ID)
        rt.commit_canon("agent", DEFAULT_AGENT_ID)
        rt.control("module", owner_id=DEFAULT_AGENT_ID, key="inter_agent", value="on")
        rt.proactive(
            "create",
            owner_id=DEFAULT_AGENT_ID,
            target_type="user",
            target_id="u1",
            intent_type="idle_share",
            summary=PUBLIC_TELLING,
            privacy_level="safe_to_share",
        )
        out = run_inter_agent_sharing_for_tick(
            rt.conn,
            ("agent", DEFAULT_AGENT_ID),
            control=ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID),
        )
        assert out == {
            "ok": True,
            "status": "skipped",
            "reason": "no active peers",
            "sources": ["proactive_intents.idle_share:user/self_journal", "diary_entries:public"],
        }
        assert rt.conn.execute("SELECT COUNT(*) FROM inter_agent_outbox").fetchone()[0] == 0
    finally:
        rt.close()


def test_agent_targeted_proactive_routes_to_inter_agent_not_user_outbox(monkeypatch, tmp_path: Path) -> None:
    """target_type=agent 的 proactive intent 走 inter-agent channel，不进用户 outbox。"""
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        agent_b = _setup_two_agents(rt)
        rt.control("module", owner_id=DEFAULT_AGENT_ID, key="inter_agent", value="on")
        rt.control("module", owner_id=DEFAULT_AGENT_ID, key="proactive", value="auto_send")
        created = rt.proactive(
            "create",
            owner_id=DEFAULT_AGENT_ID,
            target_type="agent",
            target_id=agent_b,
            intent_type="idle_share",
            summary=PUBLIC_TELLING,
            privacy_level="safe_to_share",
            importance=95,
            urgency=90,
            novelty=90,
            relationship_relevance=90,
        )
        intent_id = created["results"][0]["result"]["id"]

        evaluated = rt.proactive("evaluate", owner_id=DEFAULT_AGENT_ID, intent_id=intent_id)
        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "inter_agent_delivered"
        assert item["inter_agent_outbox"]["dedup_key"] == f"proactive-agent:{intent_id}:{agent_b}"
        assert rt.proactive("outbox", owner_id=DEFAULT_AGENT_ID)["outbox"] == []
        assert rt.conn.execute("SELECT COUNT(*) FROM inter_agent_outbox").fetchone()[0] == 1
        assert rt.conn.execute(
            "SELECT COUNT(*) FROM rumors WHERE owner_kind='agent' AND owner_id=? AND target_kind='inter_agent_outbox'",
            (agent_b,),
        ).fetchone()[0] == 1
    finally:
        rt.close()
