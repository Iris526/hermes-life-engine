"""Hermes hook callbacks for LifeEngine."""

from __future__ import annotations

import logging

from .runtime import LifeEngineRuntime
from .trace import append_audit

logger = logging.getLogger(__name__)


def pre_llm_call(**kwargs):
    rt = LifeEngineRuntime()
    try:
        if not rt.should_mount_context_for_turn(
            session_id=kwargs.get("session_id"),
            turn_id=kwargs.get("turn_id"),
            sender_id=kwargs.get("sender_id"),
            platform=kwargs.get("platform"),
        ):
            return None
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
    # Ensure DB/control exists early, then stay silent.
    rt = LifeEngineRuntime()
    try:
        rt.startup_check("agent", "default-agent", source="on_session_start")
    finally:
        rt.close()
    return None


def on_session_end(**kwargs):
    return None


def _event_attr(event, *names):
    for name in names:
        try:
            if isinstance(event, dict) and name in event:
                return event.get(name)
            value = getattr(event, name, None)
            if value is not None:
                return value
        except Exception:
            continue
    return None


def pre_gateway_dispatch(**kwargs):
    """Gateway-level ReplyGate.

    When reply_gate=auto/strict and the agent is asleep or in an
    uninterruptible event, ordinary messages are queued as delayed replies and
    skipped.  Call-like messages wake the agent.  In advisory mode we only write
    a gate decision and let the normal turn proceed.
    """
    event = kwargs.get("event")
    text = _event_attr(event, "text", "content", "message") or ""
    sender_id = _event_attr(event, "sender_id", "user_id", "from_user", "author_id") or kwargs.get("sender_id")
    session_id = _event_attr(event, "session_id", "conversation_id", "chat_id") or kwargs.get("session_id")
    turn_id = _event_attr(event, "turn_id", "message_id", "id") or kwargs.get("turn_id")
    platform = _event_attr(event, "platform") or kwargs.get("platform") or "gateway"
    rt = LifeEngineRuntime()
    try:
        out = rt.assess_incoming_message(session_id=str(session_id) if session_id else None,
                                         turn_id=str(turn_id) if turn_id else None,
                                         sender_id=str(sender_id) if sender_id else None,
                                         platform=str(platform) if platform else None,
                                         text=str(text or ""))
        if out.get("delayed_reply"):
            return {"action": "skip", "reason": "LifeEngine ReplyGate deferred message until the agent is available"}
        return {"action": "allow"}
    except Exception as exc:
        logger.debug("LifeEngine pre_gateway_dispatch ReplyGate failed: %s", exc)
        return {"action": "allow"}
    finally:
        rt.close()
