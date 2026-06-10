"""Hermes hook callbacks for LifeEngine."""

from __future__ import annotations

import logging
import os

from .runtime import LifeEngineRuntime
from .trace import append_audit

logger = logging.getLogger(__name__)


def _allowed_platforms() -> set[str]:
    """Return platforms where LifeEngine hooks are allowed to affect turns.

    Default is qqbot-only so enabling the plugin in a shared gateway profile
    does not inject LifeContext or FinalGate into Feishu/work sessions. Override
    with HERMES_LIFEENGINE_PLATFORMS=qqbot,telegram or '*' for all platforms.
    CLI/maintenance calls that do not provide a platform are left alone.
    """
    raw = os.getenv("HERMES_LIFEENGINE_PLATFORMS", "qqbot").strip()
    if raw in {"*", "all", "ALL"}:
        return {"*"}
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _platform_allowed(kwargs: dict) -> bool:
    platform = (kwargs.get("platform") or "").strip().lower()
    if not platform:
        return True
    allowed = _allowed_platforms()
    return "*" in allowed or platform in allowed


def pre_llm_call(**kwargs):
    if not _platform_allowed(kwargs):
        return None
    rt = LifeEngineRuntime()
    try:
        context = rt.build_context_for_turn(
            session_id=kwargs.get("session_id"),
            turn_id=kwargs.get("turn_id"),
            user_message=kwargs.get("user_message") or "",
            sender_id=kwargs.get("sender_id"),
            platform=kwargs.get("platform"),
            model=kwargs.get("model"),
        )
        return {"context": context}
    finally:
        rt.close()


def transform_llm_output(**kwargs):
    if not _platform_allowed(kwargs):
        return None
    rt = LifeEngineRuntime()
    try:
        return rt.audit_final_output(
            response_text=kwargs.get("response_text") or "",
            session_id=kwargs.get("session_id"),
            turn_id=kwargs.get("turn_id"),
            model=kwargs.get("model"),
            platform=kwargs.get("platform"),
            sender_id=kwargs.get("sender_id"),
        )
    finally:
        rt.close()


def post_tool_call(**kwargs):
    if not _platform_allowed(kwargs):
        return None
    # Tool observations become trace/audit, but not life facts unless the model
    # commits explicit LifeOps.  This avoids tool-result pollution.
    tool_name = kwargs.get("tool_name")
    if not tool_name:
        return None
    if str(tool_name).startswith("life_"):
        return None
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            append_audit(
                rt.conn,
                "agent",
                kwargs.get("agent_id") or "default-agent",
                "tool_observation",
                "info",
                f"Observed non-LifeEngine tool call: {tool_name}",
                {
                    "tool_name": tool_name,
                    "session_id": kwargs.get("session_id"),
                    "task_id": kwargs.get("task_id"),
                    "tool_call_id": kwargs.get("tool_call_id"),
                },
            )
    except Exception as exc:
        logger.debug("LifeEngine post_tool_call audit failed: %s", exc)
    finally:
        rt.close()
    return None


def on_session_start(**kwargs):
    if not _platform_allowed(kwargs):
        return None
    # Ensure DB/control exists early, then stay silent.
    rt = LifeEngineRuntime()
    try:
        rt.status("agent", "default-agent")
    finally:
        rt.close()
    return None


def on_session_end(**kwargs):
    return None
