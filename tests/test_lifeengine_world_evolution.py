"""heartbeat 世界演化：条件过期、流言降温、声望回归。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path) -> Path:
    """为单个测试创建隔离 HERMES_HOME，避免 SQLite profile 串库。"""
    home = tmp_path / "hermes_home_world_evolution"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime) -> None:
    """激活测试 Agent，并关闭与 world_evolution 无关的 heartbeat 模块。"""
    rt.setup("world evolution test agent")
    rt.commit_canon()
    rt.control("resume")
    for key in (
        "autonomy",
        "personality_drift",
        "reflection",
        "meals",
        "venture",
        "campaigns",
        "daily_rhythm",
        "companion",
        "proactive",
        "managed_review_loop",
        "passive_metabolism",
        "life_author",
    ):
        rt.control("module", key=key, value="off")


def result(commit: dict, index: int = 0) -> dict:
    """读取 LifeOps commit 中指定位置的领域结果。"""
    return ((commit.get("results") or [])[index].get("result") or {})


def condition_by_key(rt: LifeEngineRuntime, key: str) -> dict:
    """按稳定 key 读取 world_conditions 行。"""
    row = rt.conn.execute("SELECT * FROM world_conditions WHERE key=?", (key,)).fetchone()
    return dict(row) if row else {}


def rumor_by_id(rt: LifeEngineRuntime, rumor_id: str) -> dict:
    """按 id 读取 rumors 行。"""
    row = rt.conn.execute("SELECT * FROM rumors WHERE id=?", (rumor_id,)).fetchone()
    return dict(row) if row else {}


def reputation_account(rt: LifeEngineRuntime, account_id: str) -> dict:
    """按 id 读取 reputation_accounts 行。"""
    row = rt.conn.execute("SELECT * FROM reputation_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else {}


def heartbeat_op_count(rt: LifeEngineRuntime, op_type: str) -> int:
    """统计 world_evolution heartbeat 通过 LifeOps 提交的指定 op 数量。"""
    return int(rt.conn.execute(
        """SELECT COUNT(*)
             FROM life_ops op
             JOIN life_transactions tx ON tx.id=op.transaction_id
            WHERE op.op_type=? AND tx.source='heartbeat_world_evolution'""",
        (op_type,),
    ).fetchone()[0])


def heartbeat_receipt_count(rt: LifeEngineRuntime) -> int:
    """统计 world_evolution heartbeat 产生的 commit receipt 数量。"""
    return int(rt.conn.execute(
        """SELECT COUNT(*)
             FROM commit_receipts r
             JOIN life_transactions tx ON tx.id=r.transaction_id
            WHERE tx.source='heartbeat_world_evolution'""",
    ).fetchone()[0])


def heartbeat_journal_count(rt: LifeEngineRuntime, entry_type: str) -> int:
    """统计 world_evolution heartbeat 产生的指定 journal entry 数量。"""
    return int(rt.conn.execute(
        "SELECT COUNT(*) FROM life_journal WHERE entry_type=? AND source='heartbeat_world_evolution'",
        (entry_type,),
    ).fetchone()[0])


def test_world_evolution_expires_past_conditions_through_lifeops(tmp_path):
    """过期动态条件必须经 WORLD_UPSERT_CONDITION 变成 expired，并留下审计链。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.world(
            "condition",
            key="market_fog",
            title="Market fog",
            condition_type="hazard",
            severity=40,
            intensity=55,
            starts_at="2026-06-01T08:00:00+00:00",
            ends_at="2026-06-01T09:00:00+00:00",
            status="active",
        )
        assert condition_by_key(rt, "market_fog")["status"] == "active"

        out = rt.tick(now="2026-06-01T10:00:00+00:00", manual=False)

        assert out["world_evolution"]["status"] == "ok"
        assert condition_by_key(rt, "market_fog")["status"] == "expired"
        assert heartbeat_op_count(rt, "WORLD_UPSERT_CONDITION") == 1
        assert heartbeat_receipt_count(rt) >= 1
        assert heartbeat_journal_count(rt, "world_upsert_condition") == 1

        before = heartbeat_op_count(rt, "WORLD_UPSERT_CONDITION")
        rt.tick(now="2026-06-01T10:00:00+00:00", manual=False)
        assert heartbeat_op_count(rt, "WORLD_UPSERT_CONDITION") == before
        assert condition_by_key(rt, "market_fog")["status"] == "expired"
    finally:
        rt.close()


def test_world_evolution_decays_and_fades_rumors_idempotently(tmp_path):
    """流言热度按 elapsed hours 降温，低热老流言变 faded，同一 now 重跑不二次扣减。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        hot = result(rt.social(
            "rumor",
            content="A busy notice-board rumor",
            channel="notice_board",
            heat=0.8,
            credibility=0.4,
            effective_at="2026-06-01T00:00:00+00:00",
        ))
        low = result(rt.social(
            "rumor",
            content="A nearly forgotten rumor",
            channel="notice_board",
            heat=0.12,
            credibility=0.3,
            effective_at="2026-05-30T00:00:00+00:00",
        ))

        rt.tick(now="2026-06-02T00:00:00+00:00", manual=False)

        hot_after = rumor_by_id(rt, hot["id"])
        low_after = rumor_by_id(rt, low["id"])
        assert hot_after["status"] == "active"
        assert hot_after["heat"] == pytest.approx(0.32)
        assert low_after["status"] == "faded"
        assert low_after["heat"] == pytest.approx(0.0)
        assert heartbeat_op_count(rt, "SOCIAL_RUMOR_DECAY") == 2
        assert heartbeat_journal_count(rt, "social_rumor_decay") == 2

        before_ops = heartbeat_op_count(rt, "SOCIAL_RUMOR_DECAY")
        before_heat = rumor_by_id(rt, hot["id"])["heat"]
        rt.tick(now="2026-06-02T00:00:00+00:00", manual=False)
        assert heartbeat_op_count(rt, "SOCIAL_RUMOR_DECAY") == before_ops
        assert rumor_by_id(rt, hot["id"])["heat"] == before_heat
        assert rumor_by_id(rt, low["id"])["status"] == "faded"
    finally:
        rt.close()


def test_world_evolution_regresses_sedentary_reputation_without_overshoot(tmp_path):
    """久无声望事件的 account 按整窗小步回归中性基线，且同一 now 幂等。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        entity = result(rt.social("create_entity", entity_kind="person", display_name="Test subject"))
        seeded = result(rt.social(
            "reputation_event",
            subject_entity_id=entity["id"],
            axis="standing",
            delta=9,
            reason="old public praise",
            effective_at="2026-06-01T00:00:00+00:00",
        ))
        account_id = seeded["account"]["id"]
        assert reputation_account(rt, account_id)["value"] == pytest.approx(9.0)

        rt.tick(now="2026-06-22T00:00:00+00:00", manual=False)

        regressed = reputation_account(rt, account_id)
        assert regressed["value"] == pytest.approx(3.0)
        assert 0.0 <= regressed["value"] < 9.0
        assert heartbeat_op_count(rt, "SOCIAL_REPUTATION_EVENT") == 1
        event = rt.conn.execute(
            """SELECT * FROM reputation_events
                WHERE source='heartbeat_world_evolution' AND evidence_id=?""",
            (account_id,),
        ).fetchone()
        assert event is not None
        assert event["reason"] == "sedentary regression"
        assert event["delta"] == pytest.approx(-6.0)

        before_ops = heartbeat_op_count(rt, "SOCIAL_REPUTATION_EVENT")
        rt.tick(now="2026-06-22T00:00:00+00:00", manual=False)
        assert heartbeat_op_count(rt, "SOCIAL_REPUTATION_EVENT") == before_ops
        assert reputation_account(rt, account_id)["value"] == pytest.approx(3.0)
    finally:
        rt.close()


def test_world_evolution_gate_off_skips_world_changes(tmp_path):
    """world_evolution gate 关闭时不推进任何世界状态。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.control("module", key="world_evolution", value="off")
        rt.world(
            "condition",
            key="stale_alarm",
            title="Stale alarm",
            ends_at="2026-06-01T09:00:00+00:00",
            status="active",
        )

        out = rt.tick(now="2026-06-01T10:00:00+00:00", manual=False)

        assert out["world_evolution"]["status"] == "skipped"
        assert out["world_evolution"]["reason"] == "gate=off"
        assert condition_by_key(rt, "stale_alarm")["status"] == "active"
        assert heartbeat_op_count(rt, "WORLD_UPSERT_CONDITION") == 0
    finally:
        rt.close()
