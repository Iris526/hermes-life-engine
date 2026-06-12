import json
import re
import tempfile

import pytest

from lifeengine.runtime import LifeEngineRuntime


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_context_platform_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def _ctx_json(ctx: str):
    m = re.search(r"<LIFEENGINE_CONTEXT[^>]*>\s*(.*?)\s*</LIFEENGINE_CONTEXT>", ctx, flags=re.S)
    assert m, ctx[:500]
    return json.loads(m.group(1))


def test_feishu_context_omits_private_qq_canon_even_for_config_turns():
    rt = LifeEngineRuntime()
    try:
        rt.setup("完整 Canon 修正：我是 Iris，生活在后废土重建文明时代。")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t1", "看看世界观和 canon 设定", platform="feishu")
        data = _ctx_json(ctx)
        text = json.dumps(data, ensure_ascii=False)
        assert data["context_profile"] == "work_compact"
        assert data["mode"] == "progressive_slim_context"
        assert "raw_world_description" not in text
        assert "后废土重建文明时代" not in text
        assert "超时空朋友" not in text
        assert "Use LifeEngine tools to commit" not in text
        assert "private_agent_life_omitted" in text
        assert len(text) < 9000
    finally:
        rt.close()


def test_qq_context_can_keep_agent_life_profile_for_config_turns():
    rt = LifeEngineRuntime()
    try:
        rt.setup("完整 Canon 修正：我是 Iris，生活在后废土重建文明时代。")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t1", "看看世界观和 canon 设定", platform="qqbot")
        data = _ctx_json(ctx)
        text = json.dumps(data, ensure_ascii=False)
        assert data["context_profile"] == "agent_life"
        assert data.get("private_agent_life_omitted") is False
    finally:
        rt.close()
