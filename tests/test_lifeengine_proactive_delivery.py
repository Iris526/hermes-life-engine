from __future__ import annotations

import json
import shlex
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lifeengine.heartbeat import run_tick_script_once
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "hermes_home_delivery"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in (
        "LIFEENGINE_PROACTIVE_DELIVERY_MODE",
        "LIFEENGINE_PROACTIVE_DELIVERY_COMMAND",
        "LIFEENGINE_PROACTIVE_DELIVERY_WEBHOOK_URL",
        "LIFEENGINE_PROACTIVE_DELIVERY_TIMEOUT",
        "LIFEENGINE_PROACTIVE_DELIVERY_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    rt.setup("测试 Agent，允许主动聊天，主动消息允许排入 outbox。")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="proactive", value="auto_send")


def _queue_outbox(rt: LifeEngineRuntime, *, draft_text: str = "我想主动告诉你一声。") -> str:
    created = rt.proactive(
        "create",
        summary="我完成了一件小事，想主动告诉用户。",
        target_type="user",
        target_id="u1",
        intent_type="report_progress",
        importance=95,
        urgency=90,
        novelty=80,
        relationship_relevance=90,
        privacy_level="safe_to_share",
    )
    intent_id = created["results"][0]["result"]["id"]
    evaluated = rt.proactive("evaluate", intent_id=intent_id, draft_text=draft_text)
    item = evaluated["results"][0]["result"]["evaluated"][0]
    assert item["decision"] == "outbox_queued"
    return item["outbox"]["id"]


def _set_quiet_hours_covering_now(rt: LifeEngineRuntime) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=1)).strftime("%H:%M")
    row = rt.conn.execute(
        "SELECT id, data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id='default-agent' AND status='active' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    data = json.loads(row["data_json"])
    data.setdefault("proactive", {})
    data["proactive"].update({
        "timezone": "UTC",
        "quiet_hours": {"start": start, "end": end, "timezone": "UTC"},
    })
    rt.conn.execute("UPDATE canon_versions SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
    return {"start": start, "end": end}


def _success_command(tmp_path: Path, sink: Path) -> str:
    script = tmp_path / "deliver_success.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "payload = json.load(sys.stdin)",
                f"Path({str(sink)!r}).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
                "print(json.dumps({'ok': True, 'external_id': payload['outbox_id']}))",
            ]
        ),
        encoding="utf-8",
    )
    return " ".join([shlex.quote(sys.executable), shlex.quote(str(script))])


def _failure_command(tmp_path: Path) -> str:
    script = tmp_path / "deliver_failure.py"
    script.write_text("import sys\nsys.stderr.write('adapter down')\nsys.exit(7)\n", encoding="utf-8")
    return " ".join([shlex.quote(sys.executable), shlex.quote(str(script))])


def _slow_success_command(tmp_path: Path, sink: Path, delay: float = 0.8) -> str:
    script = tmp_path / "deliver_slow_success.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys, time",
                "from pathlib import Path",
                "payload = json.load(sys.stdin)",
                f"Path({str(sink)!r}).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
                f"time.sleep({delay!r})",
                "print(json.dumps({'ok': True, 'external_id': payload['outbox_id']}))",
            ]
        ),
        encoding="utf-8",
    )
    return " ".join([shlex.quote(sys.executable), shlex.quote(str(script))])


def test_proactive_deliver_command_marks_outbox_sent(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "payload.json"
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="我把这件事推进完了，想告诉你。")
        result = rt.proactive(
            "deliver",
            delivery_mode="command",
            delivery_command=_success_command(tmp_path, sink),
            delivery_channel="qq",
        )

        assert result["ok"] is True
        assert result["delivered"][0]["outbox_id"] == outbox_id
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "sent"
        attempts = rt.conn.execute("SELECT * FROM proactive_deliveries WHERE outbox_id=?", (outbox_id,)).fetchall()
        assert len(attempts) == 1
        assert attempts[0]["status"] == "done"
        payload = json.loads(sink.read_text(encoding="utf-8"))
        assert payload["message_text"] == "我把这件事推进完了，想告诉你。"
        assert payload["delivery_channel"] == "qq"
    finally:
        rt.close()


def test_proactive_deliver_failure_keeps_outbox_queued(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt)
        result = rt.proactive(
            "deliver",
            delivery_mode="command",
            delivery_command=_failure_command(tmp_path),
        )

        assert result["ok"] is False
        assert result["failed"][0]["outbox_id"] == outbox_id
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "queued"
        assert "adapter down" in (outbox[outbox_id]["error"] or "")
        attempt = rt.conn.execute("SELECT * FROM proactive_deliveries WHERE outbox_id=?", (outbox_id,)).fetchone()
        assert attempt["status"] == "failed"
    finally:
        rt.close()


def test_proactive_deliver_defers_queued_outbox_during_quiet_hours(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "quiet_payload.json"
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条要等天亮再说。")
        _set_quiet_hours_covering_now(rt)

        result = rt.proactive(
            "deliver",
            delivery_mode="command",
            delivery_command=_success_command(tmp_path, sink),
            delivery_channel="qq",
        )

        assert result["ok"] is True
        assert result["status"] == "deferred_quiet_hours"
        assert result["quiet_hours"]["active"] is True
        assert result["quiet_hours"]["deferred_count"] == 1
        assert result["delivered"] == []
        assert result["candidate_count"] == 0
        assert not sink.exists()
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "queued"
        assert outbox[outbox_id]["send_after"] == result["quiet_hours"]["next_allowed_at"]
        assert rt.conn.execute("SELECT COUNT(*) FROM proactive_deliveries WHERE outbox_id=?", (outbox_id,)).fetchone()[0] == 0
    finally:
        rt.close()


def test_review_treats_future_send_after_outbox_as_waiting_not_manual_action(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "quiet_review_payload.json"
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="我等安静时段过了再轻轻说。")
        _set_quiet_hours_covering_now(rt)

        delivered = rt.proactive(
            "deliver",
            delivery_mode="command",
            delivery_command=_success_command(tmp_path, sink),
            delivery_channel="qq",
        )

        assert delivered["status"] == "deferred_quiet_hours"
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "proactive_outbox" and i["source_id"] == outbox_id)

        assert item["severity"] == "info"
        assert item["title"] == "主动消息已排队，等安静时段结束再送"
        assert item["action_hint"]["action"] == "wait_until_send_after"
        assert item["action_hint"]["send_after"] == delivered["quiet_hours"]["next_allowed_at"]
        assert "等待到：" in review["rendered"]
        assert "action=wait_until_send_after" in review["rendered"]
        assert "需要人工放行" not in item["message"]
        assert not sink.exists()
    finally:
        rt.close()


def test_proactive_deliver_dry_run_does_not_reap_stale_claim(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt)
        rt.conn.execute("UPDATE proactive_outbox SET status='delivering' WHERE id=?", (outbox_id,))
        rt.conn.execute(
            """INSERT INTO proactive_deliveries(
                 id, outbox_id, intent_id, agent_id, target_user_id, status,
                 delivery_channel, payload_json, created_at
               ) VALUES(?,?,?,?,?,?,?,?,datetime('now','-60 minutes'))""",
            ("prodel_stale_dryrun", outbox_id, "intent_dryrun", "default-agent", "u1", "running", "qq", "{}"),
        )

        result = rt.proactive("deliver", dry_run=True, delivery_mode="command", delivery_command=_failure_command(tmp_path))

        assert result["status"] == "dry_run"
        assert result["stale_claims"]["skipped"] is True
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "delivering"
        attempt = rt.conn.execute("SELECT status FROM proactive_deliveries WHERE id='prodel_stale_dryrun'").fetchone()
        assert attempt["status"] == "running"
    finally:
        rt.close()


def test_concurrent_delivery_worker_does_not_double_send_same_outbox(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "slow_payload.json"
    command = _slow_success_command(tmp_path, sink)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="只能发一次的主动消息。")
    finally:
        rt.close()

    results: dict[str, dict] = {}

    def run_first_worker() -> None:
        worker = LifeEngineRuntime()
        try:
            results["first"] = worker.proactive("deliver", delivery_mode="command", delivery_command=command)
        finally:
            worker.close()

    thread = threading.Thread(target=run_first_worker)
    thread.start()
    deadline = time.time() + 5
    while not sink.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert sink.exists(), "first worker did not reach external adapter"

    rt2 = LifeEngineRuntime()
    try:
        second = rt2.proactive("deliver", delivery_mode="command", delivery_command=command)
    finally:
        rt2.close()
    thread.join(timeout=5)

    assert results["first"]["ok"] is True
    assert results["first"]["delivered"][0]["outbox_id"] == outbox_id
    assert second["status"] == "noop"
    assert second["candidate_count"] == 0

    rt3 = LifeEngineRuntime()
    try:
        attempts = rt3.conn.execute("SELECT * FROM proactive_deliveries WHERE outbox_id=?", (outbox_id,)).fetchall()
        outbox = {o["id"]: o for o in rt3.proactive("outbox")["outbox"]}
        assert len(attempts) == 1
        assert attempts[0]["status"] == "done"
        assert outbox[outbox_id]["status"] == "sent"
    finally:
        rt3.close()


def test_generated_heartbeat_script_runs_delivery_after_tick(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "heartbeat_payload.json"
    monkeypatch.setenv("LIFEENGINE_PROACTIVE_DELIVERY_MODE", "command")
    monkeypatch.setenv("LIFEENGINE_PROACTIVE_DELIVERY_COMMAND", _success_command(tmp_path, sink))
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="心跳之后也应该能送达。")
    finally:
        rt.close()

    run = run_tick_script_once(timeout=30)
    assert run["ok"] is True, run

    rt2 = LifeEngineRuntime()
    try:
        outbox = {o["id"]: o for o in rt2.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "sent"
        payload = json.loads(sink.read_text(encoding="utf-8"))
        assert payload["outbox_id"] == outbox_id
    finally:
        rt2.close()


def test_doctor_errors_when_queued_outbox_has_no_delivery_adapter(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _queue_outbox(rt, draft_text="这条消息如果没人送，就应该被 doctor 抓出来。")
        doctor = rt.doctor(include_samples=True)
        checks = {c["name"]: c for c in doctor["checks"]}
        assert checks["proactive_delivery"]["status"] == "error"
        assert checks["proactive_delivery"]["queued_outbox"] == 1
        assert checks["proactive_delivery"]["config"]["enabled"] is False
    finally:
        rt.close()


def test_proactive_status_and_review_explain_cooldown_state(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        row = rt.conn.execute(
            "SELECT id, data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id='default-agent' AND status='active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        data = json.loads(row["data_json"])
        data.setdefault("proactive", {})
        data["proactive"].update({"max_per_day": 5, "cooldown_minutes": 180})
        rt.conn.execute("UPDATE canon_versions SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))

        first_outbox_id = _queue_outbox(rt, draft_text="第一条已经发过了。")
        rt.proactive("send", outbox_id=first_outbox_id)
        second = rt.proactive(
            "create",
            summary="我又想告诉你一个小进展，但需要等冷却。",
            target_type="user",
            target_id="u1",
            intent_type="report_progress",
            importance=95,
            urgency=90,
            novelty=80,
            relationship_relevance=90,
            privacy_level="safe_to_share",
        )
        second_intent_id = second["results"][0]["result"]["id"]

        evaluated = rt.proactive("evaluate", intent_id=second_intent_id, draft_text="第二条应该先等一等。")

        assert evaluated["results"][0]["result"]["evaluated"][0]["decision"] == "queue_pending"
        assert evaluated["results"][0]["result"]["evaluated"][0]["reason"] == "within proactive cooldown window"
        status = rt.proactive("status")
        state = status["proactive"]["active_states"][0]
        assert state["user_id"] == "u1"
        assert state["wait_reason"] == "cooldown"
        assert state["pending_count"] == 1
        assert state["next_pending_intent"]["id"] == second_intent_id
        assert state["next_allowed_proactive_at"]
        assert status["proactive"]["counts"]["active_state_rows"] == 1

        review = rt.review("summary")

        assert review["summary"]["proactive_lifecycle"]["active_states"][0]["wait_reason"] == "cooldown"
        assert any("用户节奏 u1=冷却/1 条" in bit for bit in review["summary"]["proactive_lifecycle"]["render_bits"])
        assert "用户节奏 u1=冷却/1 条" in review["rendered"]
        assert "主动消息：正常" in review["rendered"]
    finally:
        rt.close()


def test_suppress_intent_suppresses_existing_queued_outbox(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条被撤回的梦不应该再发。")
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        intent_id = outbox[outbox_id]["intent_id"]

        rt.proactive("suppress", intent_id=intent_id, reason="用户不想收到这类主动消息")

        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "suppressed"
        assert outbox[outbox_id]["suppression_reason"] == "用户不想收到这类主动消息"
    finally:
        rt.close()


def test_delivery_skips_queued_outbox_when_intent_is_suppressed(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    sink = tmp_path / "should_not_exist.json"
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条消息被旧数据留在 queued，但 intent 已终止。")
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        intent_id = outbox[outbox_id]["intent_id"]
        rt.conn.execute("UPDATE proactive_intents SET status='suppressed' WHERE id=?", (intent_id,))

        result = rt.proactive(
            "deliver",
            delivery_mode="command",
            delivery_command=_success_command(tmp_path, sink),
        )

        assert result["status"] == "noop"
        assert result["candidate_count"] == 0
        assert not sink.exists()
    finally:
        rt.close()


def test_proactive_lifecycle_cleanup_retires_stale_outbox_and_state(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="旧 outbox 不应该继续等投递。")
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        intent_id = outbox[outbox_id]["intent_id"]
        rt.conn.execute("UPDATE proactive_intents SET status='suppressed', suppression_reason='legacy direct edit' WHERE id=?", (intent_id,))

        status = rt.proactive("status")
        assert status["proactive"]["ok"] is False
        assert status["proactive"]["stale_outbox_count"] == 1
        assert status["proactive"]["stale_state_count"] == 1

        cleaned = rt.proactive("cleanup")

        assert cleaned["retired_outbox_count"] == 1
        assert cleaned["state_rows_changed"] == 1
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "suppressed"
        assert outbox[outbox_id]["suppression_reason"] == "intent is suppressed"
        state = rt.proactive("state", user_id="u1")["state"]
        assert state["state"] == "silent"
        assert state["pending_intent_ids"] == []
        assert rt.proactive("status")["proactive"]["ok"] is True
    finally:
        rt.close()


def test_proactive_status_does_not_render_stale_pending_as_active_rhythm(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条孤儿消息需要被整理掉。")
        intent_id = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}[outbox_id]["intent_id"]
        rt.conn.execute("DELETE FROM proactive_intents WHERE id=?", (intent_id,))

        status = rt.proactive("status")

        assert status["proactive"]["ok"] is False
        assert status["proactive"]["stale_state_count"] == 1
        assert status["proactive"]["active_states"] == []
        assert status["proactive"]["counts"]["active_state_rows"] == 0

        review = rt.review("summary")

        assert "主动消息：需要整理" in review["rendered"]
        assert "陈旧待说状态 1 条" in review["rendered"]
        assert all("用户节奏" not in bit for bit in review["summary"]["proactive_lifecycle"]["render_bits"])
        assert "用户节奏" not in review["rendered"]
    finally:
        rt.close()


def test_proactive_status_keeps_valid_pending_when_state_also_has_stale_ids(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        first_outbox_id = _queue_outbox(rt, draft_text="这条旧消息已经不能再发。")
        stale_intent_id = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}[first_outbox_id]["intent_id"]
        rt.conn.execute("UPDATE proactive_intents SET status='suppressed', suppression_reason='legacy direct edit' WHERE id=?", (stale_intent_id,))

        second = rt.proactive(
            "create",
            summary="我想晚点再问一句近况。",
            target_type="user",
            target_id="u1",
            intent_type="ask_about_user",
            importance=90,
            urgency=80,
            novelty=80,
            relationship_relevance=95,
            privacy_level="safe_to_share",
        )
        valid_intent_id = second["results"][0]["result"]["id"]
        rt.proactive("evaluate", intent_id=valid_intent_id, draft_text="这句应该还在等待。")

        status = rt.proactive("status")
        state = status["proactive"]["active_states"][0]

        assert status["proactive"]["stale_state_count"] == 1
        assert state["pending_count"] == 1
        assert state["pending_intent_ids"] == [valid_intent_id]
        assert state["stale_pending_count"] == 1
        assert state["next_pending_intent"]["id"] == valid_intent_id
        assert stale_intent_id not in state["pending_intent_ids"]
    finally:
        rt.close()


def test_proactive_lifecycle_cleanup_closes_stale_running_delivery_attempt(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条投递卡住后也不应该继续显示 running。")
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        intent_id = outbox[outbox_id]["intent_id"]
        rt.conn.execute("UPDATE proactive_outbox SET status='delivering' WHERE id=?", (outbox_id,))
        rt.conn.execute("UPDATE proactive_intents SET status='suppressed', suppression_reason='legacy direct edit' WHERE id=?", (intent_id,))
        rt.conn.execute(
            """INSERT INTO proactive_deliveries(
                 id, outbox_id, intent_id, agent_id, target_user_id, status,
                 delivery_channel, payload_json, created_at
               ) VALUES(?,?,?,?,?,?,?,?,datetime('now','-20 minutes'))""",
            ("prodel_cleanup_running", outbox_id, intent_id, "default-agent", "u1", "running", "qq", "{}"),
        )

        status = rt.proactive("status")
        assert status["proactive"]["stale_outbox_count"] == 1
        assert status["proactive"]["stale_delivery_attempt_count"] == 1

        cleaned = rt.proactive("cleanup")

        assert cleaned["retired_outbox_count"] == 1
        assert cleaned["retired_outbox"][0]["closed_delivery_attempts"] == 1
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "suppressed"
        attempt = rt.conn.execute("SELECT status, error, completed_at FROM proactive_deliveries WHERE id='prodel_cleanup_running'").fetchone()
        assert attempt["status"] == "failed"
        assert "outbox retired during proactive cleanup" in attempt["error"]
        assert attempt["completed_at"] is not None
        assert rt.proactive("status")["proactive"]["stale_delivery_attempt_count"] == 0
    finally:
        rt.close()


def test_human_review_surfaces_and_applies_proactive_lifecycle_cleanup(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        outbox_id = _queue_outbox(rt, draft_text="这条孤儿消息需要被整理掉。")
        intent_id = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}[outbox_id]["intent_id"]
        rt.conn.execute("DELETE FROM proactive_intents WHERE id=?", (intent_id,))

        review = rt.review("summary")
        items = [i for i in review["items"] if i["item_type"] == "proactive_lifecycle_cleanup"]
        assert items
        assert review["summary"]["proactive_lifecycle"]["stale_outbox_count"] == 1
        assert review["summary"]["proactive_lifecycle"]["stale_state_count"] == 1
        assert "陈旧 outbox 1 条" in review["summary"]["proactive_lifecycle"]["render_bits"]
        assert "陈旧待说状态 1 条" in review["summary"]["proactive_lifecycle"]["render_bits"]
        assert items[0]["action_hint"]["stale_outbox_count"] == 1
        assert items[0]["action_hint"]["stale_state_count"] == 1
        assert "主动消息队列需要整理" in review["rendered"]
        assert "主动消息：需要整理" in review["rendered"]
        assert "陈旧 outbox 1 条" in review["rendered"]
        assert "陈旧待说状态 1 条" in review["rendered"]
        assert "数量：stale_outbox_count=1，stale_state_count=1" in review["rendered"]

        applied = rt.review("apply", item_id=items[0]["id"])

        assert applied["ok"] is True
        assert applied["applied"] is True
        assert applied["output"]["retired_outbox_count"] == 1
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "suppressed"
        assert outbox[outbox_id]["suppression_reason"] == "intent missing"
    finally:
        rt.close()
