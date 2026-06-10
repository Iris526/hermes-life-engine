"""Owner/workspace resolution for LifeEngine adapters.

Hermes currently passes session_id/turn_id/sender_id into hooks and many tool
calls. LifeEngine keeps this small resolver framework-independent so future
adapters can provide richer profile/agent ids without rewriting core services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .constants import DEFAULT_AGENT_ID, DEFAULT_USER_ID


@dataclass(frozen=True)
class OwnerScope:
    owner_kind: str
    owner_id: str
    agent_id: str
    user_id: str | None
    relationship_id: str | None
    workspace: str
    session_id: str | None = None
    turn_id: str | None = None
    platform: str | None = None
    sender_id: str | None = None


def default_agent_id() -> str:
    return os.getenv("LIFEENGINE_AGENT_ID") or os.getenv("HERMES_AGENT_ID") or DEFAULT_AGENT_ID


def default_user_id(sender_id: str | None = None) -> str:
    return sender_id or os.getenv("LIFEENGINE_USER_ID") or DEFAULT_USER_ID


def resolve_owner_scope(
    args: dict[str, Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    *,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    default_workspace: str = "agent_self",
) -> OwnerScope:
    args = args or {}
    kwargs = kwargs or {}
    sender_id = kwargs.get("sender_id") or args.get("sender_id")
    session_id = kwargs.get("session_id") or args.get("session_id")
    turn_id = kwargs.get("turn_id") or args.get("turn_id")
    platform = kwargs.get("platform") or args.get("platform")
    agent_id = args.get("agent_id") or kwargs.get("agent_id") or default_agent_id()
    user_id = args.get("user_id") or kwargs.get("user_id") or default_user_id(sender_id)
    raw = owner_kind or args.get("owner_kind") or args.get("owner") or default_workspace or "agent"
    raw = str(raw)
    if raw in {"agent", "agent_self"}:
        oid = owner_id or args.get("owner_id") or agent_id
        return OwnerScope("agent", str(oid), str(agent_id), str(user_id) if user_id else None, None, "agent_self", session_id, turn_id, platform, sender_id)
    if raw in {"user", "user_life"}:
        oid = owner_id or args.get("owner_id") or user_id
        return OwnerScope("user", str(oid), str(agent_id), str(oid), None, "user_life", session_id, turn_id, platform, sender_id)
    if raw == "relationship":
        uid = args.get("user_id") or user_id
        rid = owner_id or args.get("owner_id") or f"{agent_id}:{uid}"
        return OwnerScope("relationship", str(rid), str(agent_id), str(uid), str(rid), "relationship", session_id, turn_id, platform, sender_id)
    oid = owner_id or args.get("owner_id") or agent_id
    return OwnerScope(raw, str(oid), str(agent_id), str(user_id) if user_id else None, None, raw, session_id, turn_id, platform, sender_id)

# Back-compat helpers used by early LifeEngine adapters.
def resolve_owner(args: dict[str, Any] | None = None, *, owner_kind: str | None = None, owner_id: str | None = None,
                  sender_id: str | None = None, agent_id: str | None = None, user_id: str | None = None,
                  workspace: str | None = None) -> tuple[str, str]:
    merged = dict(args or {})
    if sender_id is not None:
        merged["sender_id"] = sender_id
    if agent_id is not None:
        merged["agent_id"] = agent_id
    if user_id is not None:
        merged["user_id"] = user_id
    if workspace is not None:
        merged["owner_kind"] = workspace
    scope = resolve_owner_scope(merged, owner_kind=owner_kind, owner_id=owner_id)
    return scope.owner_kind, scope.owner_id


def resolve_scope_from_hook(**kwargs: Any) -> OwnerScope:
    return resolve_owner_scope({}, kwargs)

# Compatibility helpers used by runtime/tools.
def resolve_owner(args: dict[str, Any] | None = None, *, owner_kind: str | None = None, owner_id: str | None = None, sender_id: str | None = None) -> tuple[str, str]:
    scope = resolve_owner_scope(args, {"sender_id": sender_id}, owner_kind=owner_kind, owner_id=owner_id)
    return scope.owner_kind, scope.owner_id


def resolve_scope_from_hook(*, session_id: str | None = None, turn_id: str | None = None, sender_id: str | None = None, platform: str | None = None, args: dict[str, Any] | None = None) -> OwnerScope:
    return resolve_owner_scope(args or {}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
