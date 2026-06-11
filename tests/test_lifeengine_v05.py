from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v05"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate_agent(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，支持库存、饭食和用户确认流。")
    rt.commit_canon()
    rt.control("resume")


def activate_user(rt: LifeEngineRuntime):
    rt.setup("用户生活记录，只能由用户确认。", "user", "u1")
    rt.commit_canon("user", "u1")
    rt.control("resume", "user", "u1")


def test_user_confirmation_propose_confirm_commits_user_life(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_user(rt)
        proposed_ops = [
            {"type": "CREATE_EVENT", "payload": {"title": "用户明天晚上健身", "status": "planned", "requires_confirmation": True}}
        ]
        p = rt.confirmation("propose", "user", "u1", ops=proposed_ops, reason="用户说也许明天健身，需要确认")
        cid = p["confirmation"]["id"]
        assert p["confirmation"]["status"] == "pending"
        c = rt.confirmation("confirm", "user", "u1", confirmation_id=cid)
        assert c["confirmation"]["status"] == "confirmed"
        assert c["commit"]["ok"] is True
        events = rt.event_tool("list", "user", "u1")["events"]
        assert any(e["title"] == "用户明天晚上健身" for e in events)
    finally:
        rt.close()


def test_user_confirmation_reject_does_not_commit(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_user(rt)
        p = rt.confirmation("propose", "user", "u1", ops=[{"type": "CREATE_MEMORY", "payload": {"content": "用户喜欢跑步"}}], reason="待确认")
        cid = p["confirmation"]["id"]
        r = rt.confirmation("reject", "user", "u1", confirmation_id=cid, note="不是事实")
        assert r["confirmation"]["status"] == "rejected"
        memories = rt.memory("search", "user", "u1", query="跑步")["memories"]
        assert not memories
    finally:
        rt.close()


def test_inventory_item_meal_and_receipt_final_gate(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        item = rt.inventory(
            "add",
            session_id="s5",
            turn_id="t5",
            name="藏青色百褶裙",
            category="clothing",
            quantity=1,
            unit="件",
            condition="new",
            location="衣柜",
            image_path="/root/.hermes/assets/iris-wardrobe/navy-pleated-skirt.jpg",
            media={"caption": "衣橱参考图"},
            source="agent_narrative_assertion",
        )
        assert item["ok"] is True
        tx_id = item["transaction_id"]
        explained = rt.traces("explain", transaction_id=tx_id)
        assert any(f["fact_kind"] == "inventory" for f in explained["facts"])
        assert rt.audit_final_output("我的衣柜里有一条藏青色百褶裙。", session_id="s5", turn_id="t5") is None
        inv = rt.inventory("list", category="clothing")["items"]
        assert inv and inv[0]["name"] == "藏青色百褶裙"
        media = inv[0]["attributes"]["media"]
        assert media["caption"] == "衣橱参考图"
        assert media["primary_image"]["path"].endswith("navy-pleated-skirt.jpg")
        assert media["references"][0]["role"] == "primary"

        updated = rt.inventory("update", item_id=inv[0]["id"], image_url="https://example.test/skirt.png")
        updated_media = updated["results"][0]["result"]["attributes"]["media"]
        assert updated_media["primary_image"]["url"] == "https://example.test/skirt.png"
        assert updated_media["caption"] == "衣橱参考图"

        meal = rt.inventory("meal", meal_type="lunch", food_items=["咖喱饭"], satisfaction=6, notes="有点辣", source="agent_retro_assertion")
        assert meal["ok"] is True
        meals = rt.inventory("meals", meal_type="lunch")["meals"]
        assert meals and "咖喱饭" in meals[0]["food_items"]
    finally:
        rt.close()


def test_inventory_consume_prevents_negative_quantity(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        item = rt.inventory("add", name="洗衣液", category="daily_supply", quantity=1, unit="瓶")
        item_id = item["results"][0]["result"]["id"]
        consumed = rt.inventory("consume", item_id=item_id, quantity=1, reason="用完了")
        assert consumed["ok"] is True
        try:
            rt.inventory("consume", item_id=item_id, quantity=1, reason="再用一次")
            assert False, "expected negative inventory validation failure"
        except Exception as exc:
            assert "negative" in str(exc) or "负" in str(exc) or "quantity" in str(exc)
    finally:
        rt.close()
