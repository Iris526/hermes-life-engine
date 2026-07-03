"""轴五 data foundation：两个 agent 的注册表与 truth-layered 讲述通道。"""

from __future__ import annotations

import json
from pathlib import Path

from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.inter_agent import deliver_inter_agent, enqueue_inter_agent
from lifeengine.jsonutil import loads
from lifeengine.registry import active_agents, tick_all_active
from lifeengine.runtime import LifeEngineRuntime


RESIDUE_TERMS = ("灵铢", "归明观", "符纸")


def _fresh_home(tmp_path: Path) -> Path:
    """为 constellation 测试创建隔离 HERMES_HOME。

    输入是 pytest tmp_path；输出是本测试专属目录。调用方会通过 monkeypatch 设置
    环境变量。函数不写 LifeEngine 数据，只负责给 runtime 初始化提供空目录。
    """
    home = tmp_path / "hermes_home_constellation"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _owner_text(rt: LifeEngineRuntime, owner_id: str) -> str:
    """收集某 agent 当前已写入的轻量状态文本。

    输入是 runtime 与 owner_id；输出覆盖 active Canon、资源定义、记忆和社会层的
    JSON 文本。调用方用它确认现代 B 的 seeded state 没有继承默认角色残留。
    函数只读测试数据库，不改变业务状态。
    """
    canon_row = rt.conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active'",
        (owner_id,),
    ).fetchone()
    resources = rt.conn.execute(
        "SELECT key, display_name FROM resource_definitions WHERE owner_kind='agent' AND owner_id=? ORDER BY key",
        (owner_id,),
    ).fetchall()
    memories = rt.conn.execute(
        "SELECT memory_type, content FROM memories WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (owner_id,),
    ).fetchall()
    entities = rt.conn.execute(
        "SELECT entity_kind, display_name, summary, metadata_json FROM world_entities WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
        (owner_id,),
    ).fetchall()
    return json.dumps(
        {
            "canon": loads(canon_row["data_json"], {}) if canon_row else {},
            "resources": [dict(row) for row in resources],
            "memories": [dict(row) for row in memories],
            "entities": [dict(row) for row in entities],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def test_two_agents_inter_agent_channel_is_unverified_and_idempotent(monkeypatch, tmp_path: Path) -> None:
    """验证两个 active agent、跨 agent outbox、truth layer 和幂等交付。

    场景包含默认 guimingguan agent A 与现代插画师 agent B。A 的 telling 通过
    inter-agent outbox 到达 B 后，只能成为 B 社会世界中的 `rumor_unverified` 和
    `peer_agent`，不能写入 B 的 memories/self_narrative。重复 deliver 不得重复插入。
    """
    monkeypatch.setenv("HERMES_HOME", str(_fresh_home(tmp_path)))
    rt = LifeEngineRuntime()
    agent_b = "agent-lin"
    telling = "今天收尾了一张画"
    try:
        rt.setup("名字是 明灯。她经营归明观，会接外勤委托。", "agent", DEFAULT_AGENT_ID)
        agent_a_canon = rt.commit_canon("agent", DEFAULT_AGENT_ID)["canon"]
        assert (agent_a_canon["data"].get("living") or {}).get("skin") == "guimingguan"

        rt.setup("名字是 凛，是现代插画师，生活在现代城市，货币用日元。", "agent", agent_b)
        agent_b_canon = rt.commit_canon("agent", agent_b)["canon"]
        assert not (agent_b_canon["data"].get("living") or {}).get("skin")
        rt.living("init_resources", owner_id=agent_b)
        seeded_b_text = _owner_text(rt, agent_b)
        assert all(term not in seeded_b_text for term in RESIDUE_TERMS)

        agents = active_agents(rt.conn)
        agent_ids = {agent["owner_id"] for agent in agents}
        assert {DEFAULT_AGENT_ID, agent_b}.issubset(agent_ids)
        assert {agent["owner_id"]: agent["name"] for agent in agents}[agent_b] == "凛"
        ticked = tick_all_active(rt, now="2026-07-04T09:00:00+08:00", manual=True)
        assert {DEFAULT_AGENT_ID, agent_b}.issubset(set(ticked["results"]))
        assert all(item["status"] in {"done", "partial"} for item in ticked["results"].values())

        outbox = enqueue_inter_agent(
            rt.conn,
            {"owner_kind": "agent", "owner_id": DEFAULT_AGENT_ID},
            {"owner_kind": "agent", "owner_id": agent_b},
            "telling",
            {"content": telling},
            dedup_key="default-agent-to-lin-painting-2026-07-04",
        )
        assert outbox["enqueued"] is True

        delivered = deliver_inter_agent(rt.conn)
        assert delivered["ok"] is True
        assert delivered["claimed_count"] == 1

        rumors = rt.conn.execute(
            """SELECT * FROM rumors
                WHERE owner_kind='agent' AND owner_id=? AND target_kind='inter_agent_outbox'
                ORDER BY created_at""",
            (agent_b,),
        ).fetchall()
        assert len(rumors) == 1
        rumor = dict(rumors[0])
        assert rumor["truth_layer"] == "rumor_unverified"
        assert telling in rumor["content"]
        evidence = loads(rumor["evidence_json"], {})
        assert evidence["from_owner_id"] == DEFAULT_AGENT_ID
        assert evidence["dedup_key"] == "default-agent-to-lin-painting-2026-07-04"

        peer_rows = rt.conn.execute(
            """SELECT * FROM world_entities
                WHERE owner_kind='agent' AND owner_id=? AND entity_kind='peer_agent' AND status='active'""",
            (agent_b,),
        ).fetchall()
        assert len(peer_rows) == 1
        peer = dict(peer_rows[0])
        assert peer["display_name"] == "明灯"
        peer_meta = loads(peer["metadata_json"], {})
        assert peer_meta["peer_owner_kind"] == "agent"
        assert peer_meta["peer_owner_id"] == DEFAULT_AGENT_ID

        peer_slot = rt.conn.execute(
            """SELECT 1 FROM worldview_slot_definitions
                WHERE owner_kind='agent' AND owner_id=?
                  AND slot_type='entity_kind' AND key='peer_agent' AND status='active'""",
            (agent_b,),
        ).fetchone()
        assert peer_slot is not None

        memory_rows = rt.conn.execute(
            "SELECT content FROM memories WHERE owner_kind='agent' AND owner_id=?",
            (agent_b,),
        ).fetchall()
        assert all(telling not in row["content"] for row in memory_rows)
        assert telling not in (rt.opinion("narrative", owner_id=agent_b)["self_narrative"] or "")

        delivered_again = deliver_inter_agent(rt.conn)
        assert delivered_again["status"] == "noop"
        assert rt.conn.execute(
            "SELECT COUNT(*) FROM rumors WHERE owner_kind='agent' AND owner_id=? AND target_kind='inter_agent_outbox'",
            (agent_b,),
        ).fetchone()[0] == 1
        assert rt.conn.execute(
            "SELECT COUNT(*) FROM world_entities WHERE owner_kind='agent' AND owner_id=? AND entity_kind='peer_agent' AND status='active'",
            (agent_b,),
        ).fetchone()[0] == 1

        duplicate = enqueue_inter_agent(
            rt.conn,
            ("agent", DEFAULT_AGENT_ID),
            ("agent", agent_b),
            "telling",
            {"content": telling},
            dedup_key="default-agent-to-lin-painting-2026-07-04",
        )
        assert duplicate["enqueued"] is False
        assert rt.conn.execute("SELECT COUNT(*) FROM inter_agent_outbox").fetchone()[0] == 1
    finally:
        rt.close()
