"""LifeEngine TruthSource resolver.

TruthSource turns Life Canon bindings into executable, traceable reads.  It is
intentionally embedded/local-first: external tools are represented as observations
that the agent records through ``life_truth(action='observe')`` after using any
Hermes tool or user-provided fact.  Resolution then uses fresh cached
observations according to the Canon binding TTL.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from .canon import ensure_control, get_active_canon
from .jsonutil import dumps, loads
from .time_utils import now_iso, to_epoch, normalized_iso
from .trace import append_audit, append_journal, new_id


_OBSERVATION_AUTHORITIES = {"external_tool", "user_current_location", "user_reported", "tool_observation"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _ttl_expires(ttl_minutes: int | None) -> tuple[str | None, int | None]:
    if ttl_minutes is None or int(ttl_minutes) <= 0:
        return None, None
    dt = _utc_now() + timedelta(minutes=int(ttl_minutes))
    return _iso(dt), int(dt.timestamp())


def _stable_json(obj: Any) -> str:
    return json.dumps(obj or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cache_key(domain: str, authority: str, parameters: dict[str, Any]) -> str:
    raw = f"{domain}|{authority}|{_stable_json(parameters)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _binding_for(canon: dict[str, Any], domain: str) -> dict[str, Any]:
    bindings = ((canon.get("truth_sources") or {}).get("bindings") or {})
    binding = dict(bindings.get(domain) or {})
    if not binding:
        if domain in {"time", "clock", "now"}:
            binding = {"domain": domain, "authority": "system_clock"}
        elif domain == "currency":
            binding = {"domain": domain, "authority": "fixed_setting", "value": "JPY"}
        elif domain == "location":
            loc = ((canon.get("identity") or {}).get("location")
                   or (canon.get("worldview") or {}).get("location")
                   or os.getenv("LIFEENGINE_USER_LOCATION"))
            binding = {"domain": domain, "authority": "fixed_setting", "value": loc} if loc else {"domain": domain, "authority": "unknown", "fallback": "unknown"}
        else:
            binding = {"domain": domain, "authority": "unknown", "fallback": "unknown"}
    binding.setdefault("domain", domain)
    binding.setdefault("authority", "unknown")
    binding.setdefault("fallback", "unknown")
    return binding


def _merged_parameters(binding: dict[str, Any], parameters: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(binding.get("parameters") or {})
    if parameters:
        merged.update(parameters)
    # A common Canon phrase is "same as user"; allow env/config to fill it.
    if merged.get("location") in {"user_current_location", "same_as_user", None}:
        env_loc = os.getenv("LIFEENGINE_USER_LOCATION") or os.getenv("HERMES_USER_LOCATION")
        if env_loc:
            merged["location"] = env_loc
    return merged


def _find_cached(conn, owner_kind: str, owner_id: str, domain: str, authority: str, parameters: dict[str, Any], allow_stale: bool = False) -> dict[str, Any] | None:
    key = _cache_key(domain, authority, parameters)
    now_ts = int(_utc_now().timestamp())
    if allow_stale:
        row = conn.execute(
            """SELECT * FROM truth_source_cache WHERE owner_kind=? AND owner_id=? AND domain=? AND cache_key=?
                 ORDER BY observed_at DESC LIMIT 1""",
            (owner_kind, owner_id, domain, key),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM truth_source_cache WHERE owner_kind=? AND owner_id=? AND domain=? AND cache_key=?
                 AND (expires_at_ts IS NULL OR expires_at_ts >= ?) ORDER BY observed_at DESC LIMIT 1""",
            (owner_kind, owner_id, domain, key, now_ts),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["parameters"] = loads(d.pop("parameters_json"), {})
    d["result"] = loads(d.pop("result_json"), {})
    return d


def _record_read(conn, owner_kind: str, owner_id: str, domain: str, authority: str, parameters: dict[str, Any],
                 result: dict[str, Any], status: str, trace_id: str | None = None, source: str = "truth_resolver",
                 ttl_minutes: int | None = None, error: str | None = None, cached_from_read_id: str | None = None) -> dict[str, Any]:
    expires_at, expires_at_ts = _ttl_expires(ttl_minutes)
    read_id = new_id("truthread")
    conn.execute(
        """INSERT INTO truth_source_reads(id, owner_kind, owner_id, domain, authority, parameters_json, result_json,
               trace_id, status, source, expires_at, expires_at_ts, error, cached_from_read_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (read_id, owner_kind, owner_id, domain, authority, dumps(parameters), dumps(result), trace_id, status, source,
         expires_at, expires_at_ts, error, cached_from_read_id),
    )
    if status in {"resolved", "observed", "simulated"}:
        cache_key = _cache_key(domain, authority, parameters)
        conn.execute(
            """INSERT INTO truth_source_cache(id, owner_kind, owner_id, domain, cache_key, authority, parameters_json,
                   result_json, observed_at, expires_at, expires_at_ts, source, read_id)
                   VALUES(?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?)
                   ON CONFLICT(owner_kind, owner_id, domain, cache_key) DO UPDATE SET
                     authority=excluded.authority, parameters_json=excluded.parameters_json, result_json=excluded.result_json,
                     observed_at=datetime('now'), expires_at=excluded.expires_at, expires_at_ts=excluded.expires_at_ts,
                     source=excluded.source, read_id=excluded.read_id""",
            (new_id("truthcache"), owner_kind, owner_id, domain, cache_key, authority, dumps(parameters), dumps(result),
             expires_at, expires_at_ts, source, read_id),
        )
    return {
        "read_id": read_id,
        "domain": domain,
        "authority": authority,
        "status": status,
        "parameters": parameters,
        "result": result,
        "expires_at": expires_at,
        "error": error,
        "cached_from_read_id": cached_from_read_id,
    }


def _resolve_fixed(binding: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    if "value" in parameters:
        value = parameters.get("value")
    elif "value" in binding:
        value = binding.get("value")
    else:
        value = None
    return {"value": value, "truth_layer": "fixed_setting"}


def _resolve_clock(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "now": now_iso(),
        "timezone": parameters.get("timezone") or os.getenv("TZ") or "UTC",
        "truth_layer": "system_clock",
    }


def _simulate(domain: str, parameters: dict[str, Any]) -> dict[str, Any]:
    # Deterministic, low-drama simulator.  This is narrative truth, not external observation.
    seed = int(hashlib.sha256(_stable_json({"domain": domain, "parameters": parameters, "date": now_iso()[:10]}).encode("utf-8")).hexdigest()[:8], 16)
    if domain == "weather":
        conditions = ["clear", "cloudy", "light_rain", "windy", "humid"]
        condition = conditions[seed % len(conditions)]
        return {"condition": condition, "summary": f"narrative weather: {condition}", "truth_layer": "agent_narrative"}
    return {"value": f"narrative:{domain}:{seed % 1000}", "truth_layer": "agent_narrative"}


def resolve_truth_source(conn, owner_kind: str, owner_id: str, domain: str, parameters: dict[str, Any] | None = None,
                         trace_id: str | None = None, allow_stale: bool = False) -> dict[str, Any]:
    if not domain:
        raise ValueError("truth domain is required")
    control = ensure_control(conn, owner_kind, owner_id)
    gates = control.get("module_gates") or {}
    if gates.get("truth_sources") == "off":
        result = {"reason": "truth_sources module off"}
        return _record_read(conn, owner_kind, owner_id, domain, "disabled", parameters or {}, result, "disabled", trace_id, error="truth_sources module off")

    canon = get_active_canon(conn, owner_kind, owner_id)
    binding = _binding_for(canon, domain)
    authority = str(binding.get("authority") or "unknown")
    params = _merged_parameters(binding, parameters)
    ttl = binding.get("freshness_ttl_minutes")
    ttl_int = int(ttl) if ttl is not None else None

    # Cached external observations are authoritative within TTL.
    if authority in _OBSERVATION_AUTHORITIES:
        cached = _find_cached(conn, owner_kind, owner_id, domain, authority, params, allow_stale=allow_stale)
        if cached:
            read = _record_read(conn, owner_kind, owner_id, domain, authority, params, cached["result"], "cached", trace_id,
                                source="truth_cache", cached_from_read_id=cached.get("read_id"))
            read["binding"] = binding
            return read

    if authority == "system_clock":
        status, result = "resolved", _resolve_clock(params)
    elif authority == "fixed_setting":
        status, result = "resolved", _resolve_fixed(binding, params)
    elif authority == "narrative_simulator":
        status, result = "simulated", _simulate(domain, params)
    elif authority in _OBSERVATION_AUTHORITIES:
        fallback = binding.get("fallback", "unknown")
        if fallback == "narrative_generate":
            status, result = "simulated", _simulate(domain, params)
        elif fallback == "use_last_known":
            cached = _find_cached(conn, owner_kind, owner_id, domain, authority, params, allow_stale=True)
            if cached:
                read = _record_read(conn, owner_kind, owner_id, domain, authority, params, cached["result"], "cached_stale", trace_id,
                                    source="truth_cache_stale", cached_from_read_id=cached.get("read_id"))
                read["binding"] = binding
                return read
            status, result = "requires_observation", {"requires": f"observe:{domain}", "truth_layer": "unknown"}
        else:
            status, result = "requires_observation", {"requires": f"observe:{domain}", "truth_layer": "unknown"}
    else:
        status, result = "unresolved", {"truth_layer": "unknown", "reason": f"unknown authority: {authority}"}

    read = _record_read(conn, owner_kind, owner_id, domain, authority, params, result, status, trace_id, ttl_minutes=ttl_int)
    read["binding"] = binding
    append_journal(conn, owner_kind, owner_id, "truth_source_resolved", {"read_id": read["read_id"], "domain": domain, "status": status}, "truth_source", canon_version=control.get("active_canon_version"))
    return read


def observe_truth_source(conn, owner_kind: str, owner_id: str, domain: str, result: dict[str, Any],
                         authority: str | None = None, parameters: dict[str, Any] | None = None,
                         source: str = "tool_observation", trace_id: str | None = None,
                         ttl_minutes: int | None = None) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("truth observation result must be an object")
    canon = get_active_canon(conn, owner_kind, owner_id)
    binding = _binding_for(canon, domain)
    auth = authority or str(binding.get("authority") or "external_tool")
    params = _merged_parameters(binding, parameters)
    ttl = ttl_minutes if ttl_minutes is not None else binding.get("freshness_ttl_minutes")
    read = _record_read(conn, owner_kind, owner_id, domain, auth, params, result, "observed", trace_id, source=source, ttl_minutes=int(ttl) if ttl is not None else None)
    read["binding"] = binding
    append_journal(conn, owner_kind, owner_id, "truth_source_observed", {"read_id": read["read_id"], "domain": domain, "authority": auth}, "truth_source")
    return read


def list_truth_sources(conn, owner_kind: str, owner_id: str, limit: int = 10) -> dict[str, Any]:
    canon = get_active_canon(conn, owner_kind, owner_id)
    bindings = ((canon.get("truth_sources") or {}).get("bindings") or {})
    reads = [dict(r) for r in conn.execute(
        "SELECT * FROM truth_source_reads WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall()]
    cache = [dict(r) for r in conn.execute(
        "SELECT * FROM truth_source_cache WHERE owner_kind=? AND owner_id=? ORDER BY observed_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall()]
    for r in reads:
        r["parameters"] = loads(r.pop("parameters_json"), {})
        r["result"] = loads(r.pop("result_json"), {})
    for r in cache:
        r["parameters"] = loads(r.pop("parameters_json"), {})
        r["result"] = loads(r.pop("result_json"), {})
    return {"bindings": bindings, "recent_reads": reads, "cache": cache}


def truth_binding_statement(domain: str, authority: str, value: Any | None = None, parameters: dict[str, Any] | None = None,
                            freshness_ttl_minutes: int | None = None, fallback: str | None = None) -> str:
    parts = [f"真相源绑定：{domain} 使用 {authority}"]
    if value is not None:
        parts.append(f"固定值 {value}")
    if parameters:
        parts.append(f"参数 {json.dumps(parameters, ensure_ascii=False, sort_keys=True)}")
    if freshness_ttl_minutes is not None:
        parts.append(f"freshness_ttl_minutes={freshness_ttl_minutes}")
    if fallback:
        parts.append(f"fallback={fallback}")
    return "；".join(parts)
