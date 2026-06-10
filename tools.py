"""Hermes tool handlers for LifeEngine."""

from __future__ import annotations

import json
from typing import Any, Callable

from .runtime import LifeEngineRuntime, resolve_owner


def _run(fn: Callable[[LifeEngineRuntime], Any]) -> str:
    rt = LifeEngineRuntime()
    try:
        out = fn(rt)
        return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2)
    finally:
        rt.close()


def life_status(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    return _run(lambda rt: rt.status(owner_kind, owner_id))




def life_doctor(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    return _run(lambda rt: rt.doctor(owner_kind=owner_kind, owner_id=owner_id, level=args.get("level", "full"), include_samples=bool(args.get("include_samples", False))))



def life_control(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.control(action, owner_kind, owner_id, **payload))


def life_setup(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    return _run(lambda rt: rt.setup(args.get("text"), owner_kind, owner_id))


def life_commit(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    commit_type = args.get("commit_type")
    session_id = kwargs.get("session_id") or args.get("session_id")
    turn_id = kwargs.get("turn_id") or args.get("turn_id")
    def work():
        rt = LifeEngineRuntime()
        try:
            if commit_type == "canon":
                return rt.commit_canon(owner_kind, owner_id, args.get("draft_id"), bool(args.get("activate", True)))
            if commit_type == "ops":
                return rt.commit_ops(args.get("ops") or [], owner_kind, owner_id, "life_commit_tool", session_id, turn_id)
            raise ValueError("commit_type must be canon or ops")
        finally:
            rt.close()
    try:
        out = work()
        return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2)


def life_resource(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action", "key"}}
    if action == "define":
        payload.setdefault("key", args.get("key"))
    if action in {"delta", "reserve"}:
        payload.setdefault("resource_key", args.get("resource_key") or args.get("key"))
        payload.setdefault("source", "life_resource_tool")
    if action == "release":
        payload.setdefault("reservation_id", args.get("reservation_id"))
    return _run(lambda rt: rt.resources(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_event(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.event_tool(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_memory(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.memory(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_tick(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    return _run(lambda rt: rt.tick(owner_kind, owner_id, args.get("now"), bool(args.get("manual", True))))


def life_diary(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.diary(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_trace(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "latest")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.traces(action, owner_kind, owner_id, **payload))


def life_final_gate(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "check")
    session_id = args.get("session_id") or kwargs.get("session_id")
    turn_id = args.get("turn_id") or kwargs.get("turn_id")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action", "session_id", "turn_id"}}
    return _run(lambda rt: rt.final_gate(action, owner_kind, owner_id, session_id, turn_id, **payload))


def life_truth(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.truth(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))

def life_confirmation(args: dict, **kwargs) -> str:
    # Confirmation is for User Life by default.
    owner_kind, owner_id = resolve_owner({**args, "owner_kind": args.get("owner_kind") or "user"}, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.confirmation(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_inventory(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.inventory(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_goal(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.goals(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_autonomy(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.autonomy(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_proactive(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.proactive(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_execution(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "list")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.execution(action, owner_kind, owner_id, kwargs.get("session_id"), kwargs.get("turn_id"), **payload))


def life_upgrade(args: dict, **kwargs) -> str:
    owner_kind, owner_id = resolve_owner(args, sender_id=kwargs.get("sender_id"))
    action = args.get("action", "check")
    payload = {k: v for k, v in args.items() if k not in {"owner_kind", "owner", "owner_id", "agent_id", "user_id", "action"}}
    return _run(lambda rt: rt.upgrade(action, owner_kind, owner_id, **payload))
