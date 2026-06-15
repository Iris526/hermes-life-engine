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
    rt.setup("测试 Agent，支持衣橱集合、饭食和用户确认流。")
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


def test_meal_record_and_receipt(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        meal = rt.meals("meal", meal_type="lunch", food_items=["咖喱饭"], satisfaction=6, notes="有点辣", source="agent_retro_assertion")
        assert meal["ok"] is True
        tx_id = meal["transaction_id"]
        explained = rt.traces("explain", transaction_id=tx_id)
        assert any(f["fact_kind"] == "meal" for f in explained["facts"])
        meals = rt.meals("meals", meal_type="lunch")["meals"]
        assert meals and "咖喱饭" in meals[0]["food_items"]
    finally:
        rt.close()


def test_collection_item_requires_collection(tmp_path):
    """collection_items cannot exist without a parent collection."""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        # Ensure default collections exist (wardrobe, shoe_cabinet, etc.)
        rt.collection("init", "agent", "default-agent")
        # Add a clothing item to the wardrobe collection
        item = rt.collection("add_item", "agent", "default-agent", collection_type="wardrobe", name="藏青色百褶裙", attributes={"category": "skirt", "color_family": "navy"})
        assert item["ok"] is True
        ci = item["item"]
        assert ci["collection_id"]  # must belong to a collection
        # List wardrobe items
        wardrobe = rt.collection("items", "agent", "default-agent", collection_type="wardrobe")
        assert any(i["name"] == "藏青色百褶裙" for i in wardrobe["items"])
    finally:
        rt.close()
