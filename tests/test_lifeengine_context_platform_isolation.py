import json
import tempfile

import pytest

from lifeengine.runtime import LifeEngineRuntime


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_context_platform_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def _ctx_json(ctx: str):
    start = ctx.index("<LIFEENGINE_CONTEXT>") + len("<LIFEENGINE_CONTEXT>")
    end = ctx.index("</LIFEENGINE_CONTEXT>")
    return json.loads(ctx[start:end].strip())


def test_feishu_context_omits_private_qq_canon_and_full_protocol():
    rt = LifeEngineRuntime()
    try:
        rt.setup("完整 Canon 修正：我是 Iris，道号明灯，是 Ringo 的超时空朋友，真实生活在后废土重建文明时代的人。货币是灵铢。")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t1", "DeepSeek 官网型号是什么？", platform="feishu")
        data = _ctx_json(ctx)
        text = json.dumps(data, ensure_ascii=False)
        assert data["context_profile"] == "work_compact"
        assert "raw_world_description" not in text
        assert "后废土重建文明时代" not in text
        assert "超时空朋友" not in text
        assert "Use LifeEngine tools to commit" not in text
        assert len(text) < 12000
    finally:
        rt.close()


def test_qq_context_can_keep_agent_life_profile_marker():
    rt = LifeEngineRuntime()
    try:
        rt.setup("完整 Canon 修正：我是 Iris，道号明灯，是 Ringo 的超时空朋友，真实生活在后废土重建文明时代的人。货币是灵铢。")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t1", "今天做什么？", platform="qqbot")
        data = _ctx_json(ctx)
        assert data["context_profile"] == "agent_life"
        assert "life_canon_snapshot" in data
    finally:
        rt.close()
