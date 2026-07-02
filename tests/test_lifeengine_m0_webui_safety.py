"""M0 WebUI safety-net (轴四 P0) regressions.

Pins the observatory fixes that stop it lying or thrashing:
  * the 忽略 button dismisses WITHOUT executing the item's action (§4-4: it used
    to route through apply and, for safe-auto items, do the opposite of intent);
  * requires_choice items are annotated with their real option set so the UI can
    draw genuine buttons instead of a blanket 采纳;
  * snapshot_hash ignores the volatile build timestamp, so an unchanged world
    yields a stable hash and SSE can actually de-dupe instead of full-rerendering
    every 2 seconds.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app

OWNER_KIND = "user"
OWNER_ID = "anonymous-user"


def _seed_user_confirmation(tmp_path: Path) -> tuple[str, str]:
    home = tmp_path / "hermes_home_m0_webui"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    rt = LifeEngineRuntime()
    try:
        rt.setup("m0 webui test agent")
        rt.commit_canon()
        rt.control("resume")
        rt.control("resume", OWNER_KIND, OWNER_ID, reason="enable user life")
        ops = [{"type": "CREATE_EVENT", "payload": {"title": "用户明天去跑步", "event_type": "health", "status": "planned"}}]
        rt.confirmation("propose", OWNER_KIND, OWNER_ID, ops=ops, reason="用户计划需要确认")
        review = rt.review("summary", OWNER_KIND, OWNER_ID)
        item = next(i for i in review["items"] if i["item_type"] == "user_confirmation")
        return str(db_path()), item["id"]
    finally:
        rt.close()


def test_review_items_annotate_real_choices(tmp_path):
    db, _item_id = _seed_user_confirmation(tmp_path)
    reader = LifeEngineReader(db)
    items = reader.review_items(OWNER_KIND, OWNER_ID)
    conf = next(i for i in items if i["item_type"] == "user_confirmation")
    assert conf["requires_choice"] is True
    assert conf["choices"] == ["confirm", "reject"]


def test_dismiss_does_not_execute_the_action(tmp_path):
    """忽略 must resolve the item WITHOUT confirming the pending user-life op."""
    db, item_id = _seed_user_confirmation(tmp_path)
    client = TestClient(create_app(db))
    # Point the observatory at the user life-domain that owns the item.
    client.post("/api/owner", json={"owner_kind": OWNER_KIND, "owner_id": OWNER_ID})

    resp = client.post("/api/action", json={"action": "review_dismiss", "payload": {"item_id": item_id}})
    assert resp.status_code == 200

    reader = LifeEngineReader(db)
    with reader._connect() as conn:
        item = conn.execute("SELECT status FROM human_review_items WHERE id=?", (item_id,)).fetchone()
        assert item["status"] == "dismissed"
        # The proposed confirmation must NOT have been confirmed by a dismiss.
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM user_confirmations WHERE owner_kind=? AND owner_id=? AND status='confirmed'",
            (OWNER_KIND, OWNER_ID),
        ).fetchone()[0]
        assert confirmed == 0, "dismiss must not execute the action (would confirm the op)"
    # Dismissed item drops out of the inbox.
    assert not any(i["id"] == item_id for i in reader.review_items(OWNER_KIND, OWNER_ID))


def test_reader_panels_do_not_silently_blank_on_schema_drift(tmp_path):
    """trace_latest / delayed_replies queried non-existent columns and failed every
    build, rendering a live engine as an empty page. They must return real rows."""
    from lifeengine.reply_gate import create_delayed_reply

    home = tmp_path / "hermes_home_m0_drift"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    rt = LifeEngineRuntime()
    try:
        rt.setup("m0 drift agent")
        rt.commit_canon()
        rt.control("resume")
        rt.living("init_resources")
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="醒来后说", reason="t")
        db = str(db_path())
    finally:
        rt.close()

    reader = LifeEngineReader(db)
    assert reader.trace_latest(limit=10), "life_journal trace panel must not be blank on a live engine"
    assert reader.delayed_replies("agent", "default-agent"), "delayed_replies panel must not be blank when one is queued"


def test_snapshot_hash_is_stable_when_nothing_changes(tmp_path):
    db, _item_id = _seed_user_confirmation(tmp_path)
    reader = LifeEngineReader(db)
    a = reader.snapshot("agent", "default-agent")
    b = reader.snapshot("agent", "default-agent")
    # Two builds of an unchanged world must hash identically for SSE to de-dupe;
    # the volatile build timestamp is deliberately excluded from the digest.
    assert a["snapshot_hash"] == b["snapshot_hash"]
    assert a["updated_at"] and b["updated_at"]
