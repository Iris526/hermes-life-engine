from __future__ import annotations

import json
import shlex
import sys
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
