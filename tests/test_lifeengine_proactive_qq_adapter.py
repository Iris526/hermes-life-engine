from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _adapter_module():
    script = Path(__file__).parents[1] / "scripts" / "lifeengine_proactive_media_send_qq.py"
    spec = importlib.util.spec_from_file_location("lifeengine_proactive_qq_adapter", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_generation_honors_short_adapter_timeout_and_falls_back_to_text(tmp_path, monkeypatch):
    adapter = _adapter_module()
    tts = tmp_path / "tts.py"
    tts.write_text("# placeholder", encoding="utf-8")
    adapter.TTS_SCRIPT = tts
    adapter.IMAGE_CACHE = tmp_path
    monkeypatch.setenv("LIFEENGINE_PROACTIVE_TTS_TIMEOUT", "12")
    observed: dict[str, int] = {}

    def slow_tts(*args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(adapter, "_run", slow_tts)

    voice, result = adapter.make_voice("这是一条普通主动消息。", "lively", "outbox-test")

    assert observed["timeout"] == 12
    assert voice is None
    assert result["ok"] is False
    assert result["error"] == "tts_timeout"
