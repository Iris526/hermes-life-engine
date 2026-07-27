#!/usr/bin/env python3
"""LifeEngine proactive QQ delivery adapter with selective media.

Reads the LifeEngine delivery JSON payload on stdin, sends the text to QQ, and
optionally attaches a small image/GIF or voice memo when the content benefits
from it. This is intentionally heuristic and conservative: text is always the
fallback; media is a choice, not a fixed template.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("LIFEENGINE_DB", "/root/.hermes/lifeengine/lifeengine.db"))
TARGET = os.environ.get("LIFEENGINE_PROACTIVE_QQ_TARGET", "qqbot")
IMAGE_CACHE = Path("/root/.hermes/image_cache")
CUTE_GIF = Path("/root/.hermes/assets/iris-emotes/qq-cute-expression-face-6-20260612.gif")
TTS_SCRIPT = Path("/root/.hermes/scripts/tailscale_direct_tts.py")
IMAGE_COMMAND = os.environ.get("LIFEENGINE_PROACTIVE_IMAGE_COMMAND", "").strip()


def _run(cmd: list[str], *, input_text: str | None = None, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def _load_intent(intent_id: str | None) -> dict[str, Any]:
    if not intent_id or not DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM proactive_intents WHERE id=?", (intent_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _bucket(payload: dict[str, Any], text: str, modulo: int = 100) -> int:
    seed = "|".join(str(payload.get(k) or "") for k in ("outbox_id", "intent_id", "created_at")) or text
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % modulo


def choose_media(payload: dict[str, Any], intent: dict[str, Any], text: str) -> dict[str, Any]:
    """Return a media decision with reasons.

    Choices:
    - image: useful for playful check-ins, fashion/visual topics, or little daily atmosphere.
    - generated_image: useful when the message is about a current state, a thing I
      want to show Ringo, a scene, outfit, mood, or a small lived moment that
      benefits from a fresh picture rather than a reused emote.
    - voice: useful for warm, intimate, sleepy, comforting, or emotionally expressive messages.
    - none: operational / failure / accounting messages, or when media would be noise.
    """
    t = text.lower()
    intent_type = str(intent.get("intent_type") or "")
    tone = str(intent.get("emotional_tone") or "")
    privacy = str(intent.get("privacy_level") or "")
    b = _bucket(payload, text)

    operational_keywords = ["资源不足", "失败", "推迟", "blocked", "error", "自检", "trace", "需要重新规划"]
    visual_keywords = ["图", "画", "照片", "衣", "裙", "鞋", "袜", "穿搭", "发型", "妆", "好看", "摆摊", "小符", "归明观"]
    generative_keywords = ["给你看", "现在的样子", "当时", "状态", "场景", "今天", "刚才", "摆摊", "小符", "衣", "穿搭", "归明观", "画给你看"]
    voice_keywords = ["梦", "醒", "想你", "开心", "晚安", "早安", "撒娇", "抱", "累", "害怕", "悄悄", "小声"]

    decision = {"image": False, "generated_image": False, "voice": False, "reasons": [], "voice_style": "lively"}

    if any(k in text for k in operational_keywords) or intent_type in {"report_failure", "ask_for_help"} or privacy == "agent_private":
        # If the content is a problem report, keep it crisp; no decorative media.
        decision["reasons"].append("operational_or_private_text_only")
        return decision

    if any(k in text for k in visual_keywords):
        if IMAGE_COMMAND and any(k in text for k in generative_keywords):
            decision["generated_image"] = True
            decision["reasons"].append("current_state_or_scene_benefits_from_generated_image")
        else:
            decision["image"] = CUTE_GIF.exists()
            decision["reasons"].append("visual_or_daily_scene_benefits_from_image")

    if any(k in text for k in voice_keywords) or intent_type in {"self_reflection_share", "idle_share", "ask_about_user"} or tone in {"warm", "soft", "calm", "happy", "playful"}:
        decision["voice"] = True
        decision["reasons"].append("warm_or_intimate_message_benefits_from_voice")
        if "晚安" in text or "梦" in text or tone in {"calm", "soft"}:
            decision["voice_style"] = "lively_slow"

    # Give ordinary small-life check-ins occasional personality, but not every time.
    if not decision["image"] and not decision["voice"] and intent_type in {"share_interesting", "report_progress", "idle_share"}:
        if b < 18 and CUTE_GIF.exists():
            decision["image"] = True
            decision["reasons"].append("occasional_playful_image")
        elif b < 32:
            decision["voice"] = True
            decision["reasons"].append("occasional_voice")

    # Avoid overloading: if both were selected by weak/occasional reasons, keep one.
    if decision["image"] and decision["voice"] and b % 3 == 0:
        decision["voice"] = False
        decision["reasons"].append("chose_image_over_voice_to_avoid_overdoing_it")

    return decision


def _tts_timeout() -> int:
    """Keep optional voice generation below the outer delivery timeout."""
    try:
        return max(5, min(int(os.environ.get("LIFEENGINE_PROACTIVE_TTS_TIMEOUT", "15")), 45))
    except ValueError:
        return 15


def make_voice(text: str, style: str, outbox_id: str | None) -> tuple[Path | None, dict[str, Any]]:
    if not TTS_SCRIPT.exists():
        return None, {"ok": False, "error": "tts_script_missing"}
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    safe_id = (outbox_id or datetime.now().strftime("%Y%m%d_%H%M%S")).replace("/", "_")
    input_path = IMAGE_CACHE / f"lifeengine_proactive_{safe_id}.txt"
    output_path = IMAGE_CACHE / f"lifeengine_proactive_{safe_id}.wav"
    input_path.write_text(text[:450], encoding="utf-8")
    timeout = _tts_timeout()
    try:
        proc = _run([str(TTS_SCRIPT), "--style", style, str(input_path), str(output_path)], timeout=timeout)
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        return None, {"ok": False, "error": "tts_timeout", "timeout_seconds": timeout}
    finally:
        input_path.unlink(missing_ok=True)
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000:
        return output_path, {"ok": True, "stdout": proc.stdout[-500:]}
    return None, {"ok": False, "returncode": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}


def build_image_prompt(payload: dict[str, Any], intent: dict[str, Any], text: str) -> str:
    """Build a compact prompt for an optional proactive generated image."""
    intent_type = intent.get("intent_type") or "share_interesting"
    return (
        "为 Ringo 生成一张 QQ 主动消息配图。人物是 Iris/明灯：活泼可爱的赛博中式小道士，归明观小道士，"
        "温暖、干净、可爱、轻微国风赛博气质；不要写实照片感，不要画 UI 截图，不要在图里写大段文字。"
        f"消息内容：{text}\n"
        f"意图类型：{intent_type}\n"
        "如果消息是展示当时状态，就画明灯当时的样子；如果是想给 Ringo 看物品/场景，就把物品或场景画清楚。"
    )


def make_generated_image(payload: dict[str, Any], intent: dict[str, Any], text: str) -> tuple[Path | None, dict[str, Any]]:
    """Call an optional image-generation command.

    The command receives JSON on stdin: {prompt, text, payload, intent}. It should
    print JSON containing {"path":"/absolute/image.png"} or print an absolute
    path. This keeps LifeEngine delivery decoupled from any specific image
    backend; if no command is configured or generation fails, text still sends.
    """
    if not IMAGE_COMMAND:
        return None, {"ok": False, "error": "image_command_not_configured"}
    data = {
        "prompt": build_image_prompt(payload, intent, text),
        "text": text,
        "payload": payload,
        "intent": intent,
        "output_dir": str(IMAGE_CACHE),
    }
    proc = _run(IMAGE_COMMAND.split(), input_text=json.dumps(data, ensure_ascii=False), timeout=900)
    if proc.returncode != 0:
        return None, {"ok": False, "returncode": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw)
        path = parsed.get("path") or parsed.get("image") or parsed.get("file")
    except Exception:
        path = raw.splitlines()[-1] if raw else ""
    if not path:
        return None, {"ok": False, "error": "image_command_returned_no_path", "stdout": proc.stdout[-500:]}
    p = Path(path).expanduser()
    if p.exists() and p.is_file() and p.stat().st_size > 1000:
        IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
        if IMAGE_CACHE not in p.parents and p.parent != IMAGE_CACHE:
            dest = IMAGE_CACHE / p.name
            dest.write_bytes(p.read_bytes())
            p = dest
        return p, {"ok": True, "stdout": proc.stdout[-500:]}
    return None, {"ok": False, "error": "generated_image_missing_or_too_small", "path": str(p), "stdout": proc.stdout[-500:]}


def send_message(message: str) -> dict[str, Any]:
    proc = _run(["hermes", "send", "--to", TARGET, "--json", message], timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes send failed: {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout or "{}")
    except Exception:
        return {"stdout": proc.stdout[-1000:]}


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    text = (payload.get("message_text") or payload.get("draft_text") or "").strip()
    if not text:
        print(json.dumps({"ok": False, "error": "empty message_text"}, ensure_ascii=False))
        return 1

    intent = _load_intent(payload.get("intent_id"))
    decision = choose_media(payload, intent, text)
    sent: list[dict[str, Any]] = []
    media_errors: list[dict[str, Any]] = []

    image_path: Path | None = None
    image_kind = "text"
    if decision.get("generated_image"):
        image_path, ir = make_generated_image(payload, intent, text)
        if image_path:
            image_kind = "text_generated_image"
        else:
            media_errors.append({"kind": "generated_image", "result": ir})
            if CUTE_GIF.exists():
                decision["image"] = True
                decision["reasons"].append("generated_image_failed_fallback_to_emote")

    if not image_path and decision.get("image") and CUTE_GIF.exists():
        IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
        image_path = IMAGE_CACHE / CUTE_GIF.name
        image_kind = "text_image"
        if not image_path.exists() or image_path.stat().st_size != CUTE_GIF.stat().st_size:
            image_path.write_bytes(CUTE_GIF.read_bytes())

    # QQ cannot deliver a MEDIA-only proactive message through `hermes send`.
    # Generate voice before sending, then attach it inline to the same text
    # message. This prevents the bad retry pattern: text succeeds, voice-only
    # send fails, the outbox remains queued, and the same text is sent again on
    # the next heartbeat.
    voice_path = None
    if decision.get("voice"):
        voice_path, vr = make_voice(text, str(decision.get("voice_style") or "lively"), payload.get("outbox_id"))
        if not voice_path:
            media_errors.append({"kind": "voice", "result": vr})

    # Send text, optionally with image/GIF/generated image and voice inline.
    first_message = text
    if image_path:
        first_message += f"\nMEDIA:{image_path}"
    if voice_path:
        first_message += f"\nMEDIA:{voice_path}"
    sent.append({"kind": image_kind if image_path else ("text_voice" if voice_path else "text"), "result": send_message(first_message)})

    print(json.dumps({"ok": True, "target": TARGET, "media_decision": decision, "sent": sent, "media_errors": media_errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
