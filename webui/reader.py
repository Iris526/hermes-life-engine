"""Read-only SQLite bridge for the LifeEngine WebUI.

The WebUI deliberately reads the LifeEngine database through a narrow adapter.
For arbitrary selected directories it stays read-only. Operator actions are only
available when the selected DB is the active Hermes profile DB and are routed
through LifeEngineRuntime.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import sqlite3

_LOG = logging.getLogger("lifeengine.webui.reader")
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .. import world_model as _engine_world_model
from ..persona import get_persona as _engine_get_persona
from ..resources import list_resources as _engine_list_resources
from ..social_world import (
    WORLD_AUDIENCE,
    slot_advisories,
    slots_from_canon,
)


SURFACED_VIA_ENGINE: dict[str, str] = {
    # WebUI 读面已由 reader.py 内联 SQL 迁移到 engine read-model。key 是仍被
    # Observatory 读取的 schema 表，value 是实际承担读取的 engine 函数；契约测试会
    # 扫描这些函数源码，避免 SQL 移出 reader 后被误判为未 surfaced。
    "persona_traits": "lifeengine.persona.get_persona",
    "resource_accounts": "lifeengine.resources.list_resources",
    "resource_definitions": "lifeengine.resources.list_resources",
    "world_chronicle_events": "lifeengine.world_model.list_chronicle_events",
    "world_conditions": "lifeengine.world_model.list_conditions",
    "world_faction_presence": "lifeengine.world_model.list_faction_presence",
    "world_lore_entries": "lifeengine.world_model.list_lore_entries",
    "world_places": "lifeengine.world_model.list_places",
    "world_profiles": "lifeengine.world_model.list_profiles",
    "world_regions": "lifeengine.world_model.list_regions",
    "world_routes": "lifeengine.world_model.list_routes",
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _safe_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def resolve_lifeengine_db(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a user-selected LifeEngine directory or DB path."""
    if path:
        p = Path(path).expanduser().resolve()
        candidates = []
        if p.is_file():
            candidates.append(p)
        else:
            candidates.extend([
                p / "lifeengine.db",
                p / "lifeengine" / "lifeengine.db",
                p / "lifeengine_plugin" / "lifeengine.db",
            ])
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        raise FileNotFoundError(f"找不到 LifeEngine DB：{p}；请选择包含 lifeengine.db 的目录。")
    # Default Hermes profile path.
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()
    p = home / "lifeengine" / "lifeengine.db"
    if not p.exists():
        raise FileNotFoundError(f"默认 LifeEngine DB 不存在：{p}")
    return p


@dataclass
class LifeEngineDbSelection:
    db_path: Path

    @property
    def life_dir(self) -> Path:
        return self.db_path.parent

    def connect(self) -> sqlite3.Connection:
        # Read-only URI. normal tables can be read without loading sqlite-vec.
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn


class LifeEngineReader:
    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        self.selection = LifeEngineDbSelection(resolve_lifeengine_db(db_path))

    @property
    def db_path(self) -> Path:
        return self.selection.db_path

    def _connect(self) -> sqlite3.Connection:
        return self.selection.connect()

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)).fetchone()
        return row is not None

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        if not self._table_exists(conn, table):
            return set()
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    def _first(self, conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        try:
            return _rowdict(conn.execute(sql, params).fetchone())
        except sqlite3.Error as exc:
            # Don't render a schema/query failure as "no data" silently — that is
            # how an engine that IS writing life gets shown as a blank life. Log
            # the failing query head so schema drift is diagnosable.
            _LOG.warning("reader query failed (_first): %s | %s", exc, sql.split(" WHERE")[0].strip()[:100])
            return None

    def _all(self, conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error as exc:
            _LOG.warning("reader query failed (_all): %s | %s", exc, sql.split(" WHERE")[0].strip()[:100])
            return []

    def meta(self) -> dict[str, Any]:
        with self._connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        stat = self.db_path.stat()
        return {
            "db_path": str(self.db_path),
            "life_dir": str(self.selection.life_dir),
            "schema_version": version,
            "size_bytes": stat.st_size,
            "mtime": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "tables": tables,
        }

    def owners(self) -> list[dict[str, str]]:
        owners: set[tuple[str, str]] = set()
        with self._connect() as conn:
            for table in [
                "engine_control",
                "events",
                "schedule_blocks",
                "agent_realtime_state",
                "resource_accounts",
                "memories",
                "world_entities",
                "world_profiles",
                "world_regions",
                "world_places",
                "world_lore_entries",
                "world_faction_presence",
                "world_routes",
                "world_conditions",
                "world_chronicle_events",
            ]:
                if self._table_exists(conn, table):
                    try:
                        for r in conn.execute(f"SELECT DISTINCT owner_kind, owner_id FROM {table} WHERE owner_kind IS NOT NULL AND owner_id IS NOT NULL LIMIT 200"):
                            owners.add((str(r[0]), str(r[1])))
                    except sqlite3.Error:
                        pass
        if not owners:
            owners.add(("agent", "default-agent"))
        return [{"owner_kind": k, "owner_id": v} for k, v in sorted(owners)]

    def agents(self, selected_owner_kind: str, selected_owner_id: str) -> list[dict[str, Any]]:
        """读取 constellation 顶栏使用的 active agent roster。

        输入是 WebUI 当前 selected owner；输出来自 `registry.active_agents` 的
        agent 列表，并补充 `is_selected` 供前端高亮。调用方是 `/api/agents`；
        副作用仅限只读 SQLite。旧库缺少 registry 所需表时返回空列表，避免新增
        switcher 破坏单 owner 或冷启动页面。
        """
        with self._connect() as conn:
            if not (self._table_exists(conn, "canon_versions") and self._table_exists(conn, "controls")):
                return []
            try:
                from ..registry import active_agents

                agents = active_agents(conn)
            except sqlite3.Error:
                return []
        selected_kind = str(selected_owner_kind or "")
        selected_id = str(selected_owner_id or "")
        out: list[dict[str, Any]] = []
        for agent in agents:
            owner_kind = str(agent.get("owner_kind") or "")
            owner_id = str(agent.get("owner_id") or "")
            out.append({
                "owner_kind": owner_kind,
                "owner_id": owner_id,
                "name": agent.get("name") or owner_id,
                "engine_state": agent.get("engine_state") or "unknown",
                "is_selected": owner_kind == selected_kind and owner_id == selected_id,
            })
        return out

    def control(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            table = "controls" if self._table_exists(conn, "controls") else "engine_control"
            row = self._first(conn, f"SELECT * FROM {table} WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
            if not row:
                row = self._first(conn, f"SELECT * FROM {table} LIMIT 1") or {}
            for key in ["module_gates_json", "workspace_json", "heartbeat_json", "paused_json"]:
                if key in row:
                    row[key.replace("_json", "")] = _safe_json(row.get(key), {})
            if "current_workspace" in row and "workspace" not in row:
                row["workspace"] = row.get("current_workspace")
            return row

    def realtime_state(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._first(conn, "SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
            if not row:
                return {"mode": "unknown", "owner_kind": owner_kind, "owner_id": owner_id}
            for key in ["body_state_json", "mind_state_json", "environment_state_json"]:
                row[key.replace("_json", "")] = _safe_json(row.get(key), {})
            return row

    def latest_sleep_day(self, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            if not self._table_exists(conn, "sleep_day_states"):
                return None
            row = self._first(conn, "SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC, created_at DESC LIMIT 1", (owner_kind, owner_id))
            if row:
                row["body_state"] = _safe_json(row.get("body_state_json"), {})
                row["mind_state"] = _safe_json(row.get("mind_state_json"), {})
            return row

    def resources(self, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "resource_accounts"):
                return []
            if not self._table_exists(conn, "resource_definitions"):
                rows = self._all(
                    conn,
                    """SELECT resource_key, current_value, unit, capacity, state
                       FROM resource_accounts
                       WHERE owner_kind=? AND owner_id=?
                       ORDER BY resource_key""",
                    (owner_kind, owner_id),
                )
                for row in rows:
                    row["display_name"] = None
                    row["resource_class"] = None
                    row["min_value"] = None
                    row["max_value"] = None
                return rows
            engine_rows = _engine_list_resources(conn, owner_kind, owner_id)
            definitions = {
                str(row.get("key")): row
                for row in engine_rows.get("definitions", [])
            }
            out = []
            for account in engine_rows.get("accounts", []):
                definition = definitions.get(str(account.get("resource_key"))) or {}
                out.append({
                    "resource_key": account.get("resource_key"),
                    "current_value": account.get("current_value"),
                    "unit": account.get("unit"),
                    "capacity": account.get("capacity"),
                    "state": account.get("state"),
                    "display_name": definition.get("display_name"),
                    "resource_class": definition.get("resource_class"),
                    "min_value": definition.get("min_value"),
                    "max_value": definition.get("max_value"),
                })
            return out

    def current_event(self, owner_kind: str, owner_id: str, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
        state = state or self.realtime_state(owner_kind, owner_id)
        event_id = state.get("active_event_id")
        with self._connect() as conn:
            if not event_id and state.get("active_schedule_block_id") and self._table_exists(conn, "schedule_blocks"):
                block = self._first(conn, "SELECT event_id FROM schedule_blocks WHERE id=?", (state.get("active_schedule_block_id"),))
                event_id = (block or {}).get("event_id")
            # read-only fallback: if realtime state hasn't been synced yet (e.g. no
            # tick since the window opened), show whatever block covers right now,
            # unless she's asleep/replying (those modes own the avatar).
            if not event_id and (state.get("mode") not in {"asleep", "napping", "dreaming", "waiting_to_reply"}) and self._table_exists(conn, "schedule_blocks"):
                import time as _time
                now_ts = int(_time.time())
                blk = self._first(conn, """SELECT event_id FROM schedule_blocks
                       WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','scheduled','in_progress')
                         AND start_ts IS NOT NULL AND end_ts IS NOT NULL AND start_ts <= ? AND end_ts > ?
                       ORDER BY start_ts DESC LIMIT 1""", (owner_kind, owner_id, now_ts, now_ts))
                event_id = (blk or {}).get("event_id")
            if not event_id:
                return None
            if not self._table_exists(conn, "events"):
                return None
            row = self._first(conn, "SELECT * FROM events WHERE id=?", (event_id,))
            if row:
                self._decode_event(row)
            return row

    def _decode_event(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in ["tags_json", "attributes_json", "location_json", "participants_json", "interruptibility_json", "state_effects_json", "resource_costs_json", "schedule_block_ids_json", "dependency_ids_json"]:
            if key in row:
                row[key.replace("_json", "")] = _safe_json(row.get(key), [] if key.endswith("ids_json") or key == "tags_json" else {})
        return row

    def identity(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """The agent's Canon identity (display name/role) — the user-owned name,
        not the internal owner_id. Empty if Canon has no identity set."""
        with self._connect() as conn:
            if not self._table_exists(conn, "canon_versions"):
                return {}
            row = self._first(conn, "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1", (owner_kind, owner_id))
            data = _safe_json((row or {}).get("data_json"), {}) or {}
            ident = data.get("identity") or {}
            return {
                "name": ident.get("name") or ident.get("display_name"),
                "role": ident.get("role") or ident.get("title") or ident.get("occupation"),
                "gender": ident.get("gender"),
                "age": ident.get("age"),
            }

    def social_world(self, owner_kind: str, owner_id: str, limit: int = 80) -> dict[str, Any]:
        """Worldview-defined social layer: entities, factions, reputation, evaluations and rumors."""
        empty = {
            "slots": [],
            "entities": [],
            "affiliations": [],
            "edges": [],
            "reputation": [],
            "reputation_events": [],
            "evaluations": [],
            "rumors": [],
            "rumor_exposures": [],
            "requests": [],
            "request_transitions": [],
            "advisories": [],
            "counts": {
                "slots": 0,
                "entities": 0,
                "affiliations": 0,
                "edges": 0,
                "reputation": 0,
                "evaluations": 0,
                "rumors": 0,
                "requests": 0,
                "advisories": 0,
            },
        }
        with self._connect() as conn:
            canon = {}
            if self._table_exists(conn, "canon_versions"):
                row = self._first(
                    conn,
                    "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
                    (owner_kind, owner_id),
                )
                canon = _safe_json((row or {}).get("data_json"), {}) or {}

            merged_slots: dict[tuple[str, str], dict[str, Any]] = {}
            for slot in slots_from_canon(canon):
                merged_slots[(slot.get("slot_type"), slot.get("key"))] = slot
            if self._table_exists(conn, "worldview_slot_definitions"):
                rows = self._all(
                    conn,
                    "SELECT * FROM worldview_slot_definitions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY slot_type, key LIMIT ?",
                    (owner_kind, owner_id, int(limit)),
                )
                for slot in rows:
                    slot["config"] = _safe_json(slot.pop("config_json", None), {})
                    slot.setdefault("origin", "db")
                    merged_slots[(slot.get("slot_type"), slot.get("key"))] = slot
            slots = list(merged_slots.values())
            advisories = slot_advisories(conn, owner_kind, owner_id, canon=canon, limit=int(limit))

            if not self._table_exists(conn, "world_entities"):
                empty["slots"] = slots
                empty["counts"]["slots"] = len(slots)
                empty["advisories"] = advisories
                empty["counts"]["advisories"] = len(advisories)
                return empty

            entities = self._all(
                conn,
                "SELECT * FROM world_entities WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (owner_kind, owner_id, int(limit)),
            )
            for entity in entities:
                entity["traits"] = _safe_json(entity.pop("traits_json", None), {})
                entity["metadata"] = _safe_json(entity.pop("metadata_json", None), {})

            entity_names = {str(e.get("id")): str(e.get("display_name") or e.get("id")) for e in entities if e.get("id")}

            def _label(entity_id: Any) -> str:
                key = str(entity_id or "")
                if key == WORLD_AUDIENCE:
                    return "世界/默认圈层"
                return entity_names.get(key) or key

            affiliations: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_affiliations"):
                affiliations = self._all(
                    conn,
                    """SELECT a.*, s.display_name AS subject_name, s.entity_kind AS subject_kind,
                              f.display_name AS faction_name, f.entity_kind AS faction_kind
                       FROM world_affiliations a
                       LEFT JOIN world_entities s ON s.id=a.subject_entity_id
                       LEFT JOIN world_entities f ON f.id=a.faction_entity_id
                       WHERE a.owner_kind=? AND a.owner_id=? AND a.status='active'
                       ORDER BY a.updated_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in affiliations:
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["subject_name"] = item.get("subject_name") or _label(item.get("subject_entity_id"))
                    item["faction_name"] = item.get("faction_name") or _label(item.get("faction_entity_id"))

            edges: list[dict[str, Any]] = []
            if self._table_exists(conn, "social_edges"):
                edges = self._all(
                    conn,
                    """SELECT e.*, s.display_name AS source_name, s.entity_kind AS source_kind,
                              t.display_name AS target_name, t.entity_kind AS target_kind
                       FROM social_edges e
                       LEFT JOIN world_entities s ON s.id=e.source_entity_id
                       LEFT JOIN world_entities t ON t.id=e.target_entity_id
                       WHERE e.owner_kind=? AND e.owner_id=? AND e.status='active'
                       ORDER BY ABS(e.value) DESC, e.updated_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in edges:
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["source_name"] = item.get("source_name") or _label(item.get("source_entity_id"))
                    item["target_name"] = item.get("target_name") or _label(item.get("target_entity_id"))

            reputation: list[dict[str, Any]] = []
            if self._table_exists(conn, "reputation_accounts"):
                reputation = self._all(
                    conn,
                    """SELECT r.*, s.display_name AS subject_name, s.entity_kind AS subject_kind,
                              a.display_name AS audience_name, a.entity_kind AS audience_kind
                       FROM reputation_accounts r
                       LEFT JOIN world_entities s ON s.id=r.subject_entity_id
                       LEFT JOIN world_entities a ON a.id=r.audience_entity_id
                       WHERE r.owner_kind=? AND r.owner_id=? AND r.status='active'
                       ORDER BY ABS(r.value) DESC, r.updated_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in reputation:
                    item["subject_name"] = item.get("subject_name") or _label(item.get("subject_entity_id"))
                    item["audience_name"] = item.get("audience_name") or _label(item.get("audience_entity_id"))

            reputation_events: list[dict[str, Any]] = []
            if self._table_exists(conn, "reputation_events"):
                reputation_events = self._all(
                    conn,
                    """SELECT ev.*, s.display_name AS subject_name, a.display_name AS audience_name
                       FROM reputation_events ev
                       LEFT JOIN world_entities s ON s.id=ev.subject_entity_id
                       LEFT JOIN world_entities a ON a.id=ev.audience_entity_id
                       WHERE ev.owner_kind=? AND ev.owner_id=?
                       ORDER BY ev.created_at DESC LIMIT ?""",
                    (owner_kind, owner_id, min(int(limit), 30)),
                )
                for item in reputation_events:
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["subject_name"] = item.get("subject_name") or _label(item.get("subject_entity_id"))
                    item["audience_name"] = item.get("audience_name") or _label(item.get("audience_entity_id"))

            evaluations: list[dict[str, Any]] = []
            if self._table_exists(conn, "social_evaluations"):
                evaluations = self._all(
                    conn,
                    """SELECT ev.*, e.display_name AS evaluator_name, s.display_name AS subject_name
                       FROM social_evaluations ev
                       LEFT JOIN world_entities e ON e.id=ev.evaluator_entity_id
                       LEFT JOIN world_entities s ON s.id=ev.subject_entity_id
                       WHERE ev.owner_kind=? AND ev.owner_id=? AND ev.status='active'
                       ORDER BY ev.created_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in evaluations:
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["evaluator_name"] = item.get("evaluator_name") or _label(item.get("evaluator_entity_id"))
                    item["subject_name"] = item.get("subject_name") or _label(item.get("subject_entity_id"))

            rumors: list[dict[str, Any]] = []
            exposure_counts: dict[str, int] = {}
            if self._table_exists(conn, "rumor_exposures"):
                for row in self._all(
                    conn,
                    "SELECT rumor_id, COUNT(*) AS exposure_count FROM rumor_exposures WHERE owner_kind=? AND owner_id=? GROUP BY rumor_id",
                    (owner_kind, owner_id),
                ):
                    exposure_counts[str(row.get("rumor_id"))] = int(row.get("exposure_count") or 0)
            if self._table_exists(conn, "rumors"):
                rumors = self._all(
                    conn,
                    """SELECT r.*, s.display_name AS subject_name, s.entity_kind AS subject_kind
                       FROM rumors r
                       LEFT JOIN world_entities s ON s.id=r.subject_entity_id
                       WHERE r.owner_kind=? AND r.owner_id=? AND r.status='active'
                       ORDER BY r.heat DESC, r.updated_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in rumors:
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["subject_name"] = item.get("subject_name") or _label(item.get("subject_entity_id"))
                    item["exposure_count"] = exposure_counts.get(str(item.get("id")), 0)

            requests: list[dict[str, Any]] = []
            if self._table_exists(conn, "social_requests"):
                requests = self._all(
                    conn,
                    """SELECT q.*, r.display_name AS requester_name, t.display_name AS target_name
                       FROM social_requests q
                       LEFT JOIN world_entities r ON r.id=q.requester_entity_id
                       LEFT JOIN world_entities t ON t.id=q.target_entity_id
                       WHERE q.owner_kind=? AND q.owner_id=?
                       ORDER BY q.created_at DESC LIMIT ?""",
                    (owner_kind, owner_id, int(limit)),
                )
                for item in requests:
                    item["details"] = _safe_json(item.pop("details_json", None), {})
                    item["quote"] = _safe_json(item.pop("quote_json", None), {})
                    item["billing"] = _safe_json(item.pop("billing_json", None), {})
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["requester_name"] = item.get("requester_name") or _label(item.get("requester_entity_id"))
                    item["target_name"] = item.get("target_name") or _label(item.get("target_entity_id"))

            request_transitions: list[dict[str, Any]] = []
            if self._table_exists(conn, "social_request_transitions"):
                request_transitions = self._all(
                    conn,
                    """SELECT x.*, q.topic AS request_topic, q.request_type
                       FROM social_request_transitions x
                       LEFT JOIN social_requests q ON q.id=x.request_id
                       WHERE x.owner_kind=? AND x.owner_id=?
                       ORDER BY x.created_at DESC LIMIT ?""",
                    (owner_kind, owner_id, min(int(limit), 40)),
                )
                for item in request_transitions:
                    item["quote"] = _safe_json(item.pop("quote_json", None), {})
                    item["billing"] = _safe_json(item.pop("billing_json", None), {})
                    item["evidence"] = _safe_json(item.pop("evidence_json", None), {})

            rumor_exposures: list[dict[str, Any]] = []
            if self._table_exists(conn, "rumor_exposures"):
                rumor_exposures = self._all(
                    conn,
                    """SELECT x.*, e.display_name AS entity_name, r.content AS rumor_content
                       FROM rumor_exposures x
                       LEFT JOIN world_entities e ON e.id=x.entity_id
                       LEFT JOIN rumors r ON r.id=x.rumor_id
                       WHERE x.owner_kind=? AND x.owner_id=?
                       ORDER BY x.updated_at DESC LIMIT ?""",
                    (owner_kind, owner_id, min(int(limit), 40)),
                )
                for item in rumor_exposures:
                    item["entity_name"] = item.get("entity_name") or _label(item.get("entity_id"))

            return {
                "slots": slots,
                "entities": entities,
                "affiliations": affiliations,
                "edges": edges,
                "reputation": reputation,
                "reputation_events": reputation_events,
                "evaluations": evaluations,
                "rumors": rumors,
                "rumor_exposures": rumor_exposures,
                "requests": requests,
                "request_transitions": request_transitions,
                "advisories": advisories,
                "counts": {
                    "slots": len(slots),
                    "entities": len(entities),
                    "affiliations": len(affiliations),
                    "edges": len(edges),
                    "reputation": len(reputation),
                    "evaluations": len(evaluations),
                    "rumors": len(rumors),
                    "requests": len(requests),
                    "advisories": len(advisories),
                },
            }

    def world_model(self, owner_kind: str, owner_id: str, limit: int = 80,
                    current_location: dict[str, Any] | None = None,
                    actor_label: str | None = "明灯") -> dict[str, Any]:
        """读取结构化世界本体给 WebUI 使用。

        输入是 owner、条数上限和可选当前 location；输出按档案、区域、地点、知识条目、
        势力影响和 map 分组。函数只读 SQLite，不解释世界观文本含义；地图坐标和地形
        来自 profile.rules.map、region.traits.map、place.coordinates，明灯位置只从
        当前事件 location 的结构化地点引用或唯一地点名解析。
        """
        empty = {
            "profiles": [],
            "regions": [],
            "places": [],
            "lore": [],
            "faction_presence": [],
            "routes": [],
            "conditions": [],
            "chronicle_events": [],
            "map": _engine_world_model.map_state([], [], [], current_location=current_location, actor_label=actor_label),
            "counts": {
                "profiles": 0,
                "regions": 0,
                "places": 0,
                "lore": 0,
                "faction_presence": 0,
                "routes": 0,
                "conditions": 0,
                "chronicle_events": 0,
            },
        }
        with self._connect() as conn:
            profiles: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_profiles"):
                profiles = _engine_world_model.list_profiles(conn, owner_kind, owner_id, limit=int(limit))

            regions: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_regions"):
                regions = _engine_world_model.list_regions(conn, owner_kind, owner_id, limit=int(limit))
            region_names = {str(r.get("id")): str(r.get("name") or r.get("key") or r.get("id")) for r in regions if r.get("id")}

            places: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_places"):
                places = _engine_world_model.list_places(conn, owner_kind, owner_id, limit=int(limit))
                for item in places:
                    item["region_name"] = region_names.get(str(item.get("region_id") or ""))
            place_names = {str(p.get("id")): str(p.get("name") or p.get("key") or p.get("id")) for p in places if p.get("id")}

            lore: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_lore_entries"):
                lore = _engine_world_model.list_lore_entries(conn, owner_kind, owner_id, limit=int(limit))
                for item in lore:
                    scope_kind = item.get("scope_kind")
                    scope_id = str(item.get("scope_id") or "")
                    item["scope_name"] = "世界" if scope_kind == "world" else (
                        region_names.get(scope_id) if scope_kind == "region" else place_names.get(scope_id)
                    )

            faction_presence: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_faction_presence"):
                if self._table_exists(conn, "world_entities"):
                    faction_presence = _engine_world_model.list_faction_presence(conn, owner_kind, owner_id, limit=int(limit))
                else:
                    faction_presence = self._all(
                        conn,
                        """SELECT * FROM world_faction_presence
                           WHERE owner_kind=? AND owner_id=? AND status='active'
                           ORDER BY ABS(influence) DESC, updated_at DESC LIMIT ?""",
                        (owner_kind, owner_id, int(limit)),
                    )
                for item in faction_presence:
                    if "evidence_json" in item:
                        item["evidence"] = _safe_json(item.pop("evidence_json", None), {})
                    item["faction_name"] = item.get("faction_name") or item.get("faction_entity_id")
                    item.setdefault("faction_kind", None)
                    scope_kind = item.get("scope_kind")
                    scope_id = str(item.get("scope_id") or "")
                    item["scope_name"] = "世界" if scope_kind == "world" else (
                        region_names.get(scope_id) if scope_kind == "region" else place_names.get(scope_id)
                    )

            def _scope_name(scope_kind: Any, scope_id: Any) -> str | None:
                kind = str(scope_kind or "")
                sid = str(scope_id or "")
                if kind == "world":
                    return "世界"
                if kind == "region":
                    return region_names.get(sid) or sid
                if kind == "place":
                    return place_names.get(sid) or sid
                return sid or None

            routes: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_routes"):
                routes = _engine_world_model.list_routes(conn, owner_kind, owner_id, limit=int(limit))
                for item in routes:
                    item["from_scope_name"] = _scope_name(item.get("from_scope_kind"), item.get("from_scope_id"))
                    item["to_scope_name"] = _scope_name(item.get("to_scope_kind"), item.get("to_scope_id"))

            conditions: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_conditions"):
                conditions = _engine_world_model.list_conditions(conn, owner_kind, owner_id, limit=int(limit))
                for item in conditions:
                    item["scope_name"] = _scope_name(item.get("scope_kind"), item.get("scope_id"))

            chronicle_events: list[dict[str, Any]] = []
            if self._table_exists(conn, "world_chronicle_events"):
                chronicle_events = _engine_world_model.list_chronicle_events(
                    conn, owner_kind, owner_id, limit=max(int(limit), 80)
                )
                for item in chronicle_events:
                    item["scope_name"] = _scope_name(item.get("scope_kind"), item.get("scope_id"))

            if not any([profiles, regions, places, lore, faction_presence, routes, conditions, chronicle_events]):
                return empty
            world_map = _engine_world_model.map_state(
                profiles, regions, places, routes, conditions,
                current_location=current_location,
                actor_label=actor_label or "明灯",
            )
            return {
                "profiles": profiles,
                "regions": regions,
                "places": places,
                "lore": lore,
                "faction_presence": faction_presence,
                "routes": routes,
                "conditions": conditions,
                "chronicle_events": chronicle_events,
                "map": world_map,
                "counts": {
                    "profiles": len(profiles),
                    "regions": len(regions),
                    "places": len(places),
                    "lore": len(lore),
                    "faction_presence": len(faction_presence),
                    "routes": len(routes),
                    "conditions": len(conditions),
                    "chronicle_events": len(chronicle_events),
                },
            }

    def schedule(self, owner_kind: str, owner_id: str, period: str = "today", date: str | None = None, include_completed: bool = True, limit: int = 500) -> dict[str, Any]:
        start, end, label = period_range(period, date)
        with self._connect() as conn:
            if not self._table_exists(conn, "schedule_blocks"):
                return {"period": period, "label": label, "items": []}
            status_filter = "" if include_completed else "AND s.status NOT IN ('completed','cancelled','missed')"
            rows = self._all(conn, f"""
                SELECT s.*, e.title AS event_title, e.event_type, e.event_category, e.activity_domain, e.subtype,
                       e.status AS event_status, e.importance, e.priority, e.location_json, e.interruptibility_json AS event_interruptibility_json
                FROM schedule_blocks s
                LEFT JOIN events e ON e.id=s.event_id
                WHERE s.owner_kind=? AND s.owner_id=?
                  AND COALESCE(s.start_ts, strftime('%s', s.start)) < ?
                  AND COALESCE(s.end_ts, strftime('%s', s.end)) > ?
                  {status_filter}
                ORDER BY COALESCE(s.start_ts, strftime('%s', s.start)), s.start
                LIMIT ?
            """, (owner_kind, owner_id, int(end.timestamp()), int(start.timestamp()), limit))
            for r in rows:
                r["location"] = _safe_json(r.get("location_json"), {})
                r["interruptibility"] = _safe_json(r.get("interruptibility_json"), {}) or _safe_json(r.get("event_interruptibility_json"), {})
                r["is_sleep"] = (r.get("block_type") == "sleep") or (r.get("event_category") == "sleep")
            return {"period": period, "date": date, "label": label, "start": start.isoformat(), "end": end.isoformat(), "items": rows}

    def events(self, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "events"):
                return []
            if status:
                rows = self._all(conn, "SELECT * FROM events WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, status, limit))
            else:
                rows = self._all(conn, "SELECT * FROM events WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?", (owner_kind, owner_id, limit))
            return [self._decode_event(r) for r in rows]

    def clock(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """Agent local time + day-phase for the diegetic stage (day/night)."""
        tz_name = "UTC"
        with self._connect() as conn:
            if self._table_exists(conn, "canon_versions"):
                row = self._first(conn, "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1", (owner_kind, owner_id))
                canon = _safe_json((row or {}).get("data_json"), {}) or {}
                tz_name = (
                    ((canon.get("schedule_rules") or {}).get("timezone"))
                    or (((canon.get("truth_sources") or {}).get("bindings") or {}).get("time") or {}).get("timezone")
                    or "UTC"
                )
        try:
            from zoneinfo import ZoneInfo
            local = _now().astimezone(ZoneInfo(tz_name))
        except Exception:
            local = _now()
            tz_name = "UTC"
        hour = local.hour + local.minute / 60.0
        if 5 <= hour < 8:
            phase, label = "dawn", "拂晓"
        elif 8 <= hour < 17:
            phase, label = "day", "白日"
        elif 17 <= hour < 20:
            phase, label = "dusk", "黄昏"
        else:
            phase, label = "night", "夜晚"
        return {"iso": local.isoformat(), "hour": round(hour, 2), "hhmm": local.strftime("%H:%M"),
                "timezone": tz_name, "phase": phase, "label": label}

    def meals_today(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """Today's three-meal accountability for the HUD: eaten / skipped / pending."""
        tz_name = "UTC"
        times = {"breakfast": "07:30", "lunch": "12:30", "dinner": "19:00"}
        with self._connect() as conn:
            if not self._table_exists(conn, "meal_records"):
                return {"meals": []}
            if self._table_exists(conn, "canon_versions"):
                row = self._first(conn, "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1", (owner_kind, owner_id))
                canon = _safe_json((row or {}).get("data_json"), {}) or {}
                m = canon.get("meals") or {}
                times = m.get("times") or times
                tz_name = (m.get("timezone") or (canon.get("schedule_rules") or {}).get("timezone") or tz_name)
            cols = self._columns(conn, "meal_records")
            try:
                from zoneinfo import ZoneInfo
                date_key = _now().astimezone(ZoneInfo(tz_name)).date().isoformat()
            except Exception:
                date_key = _now().date().isoformat()
            by_type = {}
            if "meal_date" in cols:
                rows = self._all(conn, "SELECT meal_type, status, skip_reason FROM meal_records WHERE owner_kind=? AND owner_id=? AND meal_date=?", (owner_kind, owner_id, date_key))
                by_type = {r["meal_type"]: r for r in rows}
            order = {"breakfast": 0, "lunch": 1, "dinner": 2}
            has_brunch = "brunch" in by_type
            meals = []
            for mt, hhmm in sorted(times.items(), key=lambda kv: order.get(kv[0], 9)):
                rec = by_type.get(mt)
                if rec:
                    status = rec.get("status") or "eaten"
                elif has_brunch and mt in ("breakfast", "lunch"):
                    status = "covered"
                else:
                    status = "pending"
                meals.append({"meal_type": mt, "time": hhmm, "status": status,
                              "skip_reason": rec.get("skip_reason") if rec else None})
            # autonomously-derived extras (brunch / 下午茶 / 夜宵 …)
            extras = [{"meal_type": mt, "status": v.get("status") or "eaten", "skip_reason": v.get("skip_reason")}
                      for mt, v in by_type.items() if mt not in times]
            return {"date": date_key, "meals": meals, "extras": extras}

    def persona(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """Living-persona traits (v0.14.0) for the HUD — read-only."""
        with self._connect() as conn:
            if not self._table_exists(conn, "persona_traits"):
                return {"seeded": False, "traits": []}
            rows_by_key = _engine_get_persona(conn, owner_kind, owner_id)
            if not rows_by_key:
                return {"seeded": False, "traits": []}
            traits = []
            notable = []
            for trait_key in sorted(rows_by_key):
                r = rows_by_key[trait_key]
                value = float(r.get("value") or 0.0)
                baseline = float(r.get("baseline") or 0.0)
                t = {"key": r["trait_key"], "value": round(value, 3), "baseline": round(baseline, 3),
                     "evidence_count": int(r.get("evidence_count") or 0), "drift": round(value - baseline, 3)}
                traits.append(t)
                if abs(value) >= 0.33:
                    notable.append((r["trait_key"], value))
            notable.sort(key=lambda x: -abs(x[1]))
            tone = "、".join(f"{k}{'偏高' if v >= 0.33 else '偏低'}" for k, v in notable[:4]) or "性格平稳"
            return {"seeded": True, "traits": traits, "tone_hint": tone}

    def review_items(self, owner_kind: str, owner_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "human_review_items"):
                return []
            rows = self._all(conn, """
                SELECT * FROM human_review_items
                WHERE owner_kind=? AND owner_id=? AND COALESCE(status,'open') NOT IN ('dismissed','resolved')
                ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, created_at DESC
                LIMIT ?
            """, (owner_kind, owner_id, limit))
            from ..review import review_item_choices
            for r in rows:
                r["action_hint"] = _safe_json(r.get("action_hint_json"), {})
                # Annotate each item with whether it needs an explicit human choice
                # (and which options), so the Observatory can draw the real option
                # buttons — e.g. send/suppress, confirm/reject — instead of a blanket
                # "采纳" that silently no-ops on requires_choice items.
                r.update(review_item_choices(r))
            # v0.12.1+ WebUI UX: stale duplicate Doctor transition warnings are
            # internal maintenance noise once the Observatory can explain events
            # directly. Keep them out of the human Review Inbox surface; doctor
            # details remain available through doctor/trace endpoints.
            rows = [
                r for r in rows
                if not (
                    str(r.get("item_type") or "") == "doctor_warning"
                    and str(r.get("title") or "").strip() == "Doctor: event_transition_coverage"
                )
            ]
            return rows

    def delayed_replies(self, owner_kind: str, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "delayed_replies"):
                return []
            # delayed_replies records its creation time as queued_at (there is no
            # created_at column); the old ORDER BY created_at failed every build.
            return self._all(conn, "SELECT * FROM delayed_replies WHERE owner_kind=? AND owner_id=? ORDER BY queued_at DESC LIMIT ?", (owner_kind, owner_id, limit))

    def dreams(self, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "dream_entries"):
                return []
            rows = self._all(conn, "SELECT * FROM dream_entries WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))
            for r in rows:
                r["symbols"] = _safe_json(r.get("symbols_json"), [])
            return rows

    def campaigns(self, owner_kind: str, owner_id: str, limit: int = 12) -> list[dict[str, Any]]:
        """v0.18 资料片: cross-week themed arcs, active first then recently resolved."""
        with self._connect() as conn:
            if not self._table_exists(conn, "campaigns"):
                return []
            rows = self._all(
                conn,
                "SELECT * FROM campaigns WHERE owner_kind=? AND owner_id=? AND status IN ('active','resolved') "
                "ORDER BY (status='active') DESC, updated_at DESC LIMIT ?",
                (owner_kind, owner_id, limit),
            )
            for r in rows:
                phases = _safe_json(r.get("phases_json"), [])
                r["phases"] = phases
                r["theme"] = _safe_json(r.get("theme_json"), {})
                r["phase_count"] = len(phases)
                cp = int(r.get("current_phase") or 0)
                r["current_phase_title"] = phases[cp].get("title") if 0 <= cp < len(phases) else None
            return rows

    def goals(self, owner_kind: str, owner_id: str, limit: int = 40) -> dict[str, Any]:
        """读取 WebUI 的目标面板 read model。

        输入是 owner 与可选目标上限；输出是稳定的 goals/milestones/progress 嵌套
        结构，供 Observatory 展示“她在追的事”。调用方式为 WebUI reader/endpoints
        同步只读调用；副作用仅限读取 SQLite。缺表、缺行或查询失败时降级为空列表，
        不改变 runtime 目标写入逻辑，也不暴露 engine 内部的完整目标表字段。
        """
        with self._connect() as conn:
            if not self._table_exists(conn, "goals"):
                return {"goals": []}
            goal_rows = self._all(
                conn,
                """SELECT id, title, status, goal_type, priority, created_at, updated_at
                   FROM goals
                   WHERE owner_kind=? AND owner_id=?
                   ORDER BY CASE
                              WHEN COALESCE(status, 'active') IN ('active','open','in_progress','planned') THEN 0
                              ELSE 1
                            END,
                            priority DESC,
                            updated_at DESC,
                            created_at DESC
                   LIMIT ?""",
                (owner_kind, owner_id, int(limit)),
            )
            if not goal_rows:
                return {"goals": []}

            milestones_by_goal: dict[str, list[dict[str, Any]]] = {}
            if self._table_exists(conn, "goal_milestones"):
                for row in self._all(
                    conn,
                    """SELECT id, goal_id, title, status, due_at, completed_at
                       FROM goal_milestones
                       WHERE owner_kind=? AND owner_id=?
                       ORDER BY CASE WHEN COALESCE(status, 'planned') IN ('done','completed') THEN 1 ELSE 0 END,
                                COALESCE(due_at_ts, strftime('%s', due_at), strftime('%s', created_at)),
                                created_at""",
                    (owner_kind, owner_id),
                ):
                    item = {
                        "id": row.get("id"),
                        "title": row.get("title"),
                        "done": str(row.get("status") or "").lower() in {"done", "completed"} or bool(row.get("completed_at")),
                        "target_date": row.get("due_at"),
                    }
                    milestones_by_goal.setdefault(str(row.get("goal_id")), []).append(item)

            progress_by_goal: dict[str, list[dict[str, Any]]] = {}
            if self._table_exists(conn, "goal_progress_entries"):
                for row in self._all(
                    conn,
                    """SELECT id, goal_id, reason, delta, created_at
                       FROM goal_progress_entries
                       WHERE owner_kind=? AND owner_id=?
                       ORDER BY created_at DESC, rowid DESC""",
                    (owner_kind, owner_id),
                ):
                    goal_id = str(row.get("goal_id"))
                    bucket = progress_by_goal.setdefault(goal_id, [])
                    if len(bucket) >= 8:
                        continue
                    bucket.append({
                        "id": row.get("id"),
                        "note": row.get("reason"),
                        "delta": row.get("delta"),
                        "created_at": row.get("created_at"),
                    })

            goals = []
            for row in goal_rows:
                goal_id = str(row.get("id"))
                goals.append({
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "kind": row.get("goal_type"),
                    "priority": row.get("priority"),
                    "created_at": row.get("created_at"),
                    "milestones": milestones_by_goal.get(goal_id, []),
                    "progress": progress_by_goal.get(goal_id, []),
                })
            return {"goals": goals}

    def daily_rhythm(self, owner_kind: str, owner_id: str, date: str | None = None) -> dict[str, Any]:
        """读取 WebUI 的每日节律 read model。

        输入是 owner 与可选 YYYY-MM-DD 日期；未指定日期时读取库中最新可用日期。
        输出只包含 date 与按开始时间排序的节律 item，供 Observatory 展示日常节奏。
        调用方式为 WebUI reader/endpoints 同步只读调用；副作用仅限读取 SQLite。
        `life_rhythm_items` 或可选 run 表缺失、无匹配行或查询失败时返回空 items，
        保持旧库和空库不会 500。
        """
        with self._connect() as conn:
            if not self._table_exists(conn, "life_rhythm_items"):
                return {"date": date, "items": []}

            has_runs = self._table_exists(conn, "life_rhythm_runs")
            if date is None:
                if has_runs:
                    row = self._first(
                        conn,
                        """SELECT COALESCE(substr(i.start, 1, 10), r.date_key) AS date_key
                           FROM life_rhythm_items i
                           LEFT JOIN life_rhythm_runs r ON r.id=i.run_id
                           WHERE i.owner_kind=? AND i.owner_id=?
                             AND COALESCE(substr(i.start, 1, 10), r.date_key) IS NOT NULL
                           ORDER BY date_key DESC
                           LIMIT 1""",
                        (owner_kind, owner_id),
                    )
                else:
                    row = self._first(
                        conn,
                        """SELECT substr(start, 1, 10) AS date_key
                           FROM life_rhythm_items
                           WHERE owner_kind=? AND owner_id=? AND start IS NOT NULL
                           ORDER BY date_key DESC
                           LIMIT 1""",
                        (owner_kind, owner_id),
                    )
                date = (row or {}).get("date_key")

            if date is None:
                return {"date": None, "items": []}

            if has_runs:
                rows = self._all(
                    conn,
                    """SELECT i.id, i.title, i.start, i.end, i.category, i.activity_domain,
                              i.status, i.payload_json
                       FROM life_rhythm_items i
                       LEFT JOIN life_rhythm_runs r ON r.id=i.run_id
                       WHERE i.owner_kind=? AND i.owner_id=?
                         AND COALESCE(substr(i.start, 1, 10), r.date_key)=?
                       ORDER BY i.start, i.end, i.created_at""",
                    (owner_kind, owner_id, date),
                )
            else:
                rows = self._all(
                    conn,
                    """SELECT id, title, start, end, category, activity_domain, status, payload_json
                       FROM life_rhythm_items
                       WHERE owner_kind=? AND owner_id=? AND substr(start, 1, 10)=?
                       ORDER BY start, end, created_at""",
                    (owner_kind, owner_id, date),
                )

            items = []
            for row in rows:
                payload = _safe_json(row.get("payload_json"), {}) or {}
                items.append({
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "kind": row.get("category") or row.get("activity_domain"),
                    "status": row.get("status"),
                    "note": payload.get("note") or payload.get("description") or payload.get("summary"),
                })
            return {"date": date, "items": items}

    def inner_life(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """v0.18 心相: her current self-narrative + the opinions she holds."""
        with self._connect() as conn:
            opinions = []
            if self._table_exists(conn, "agent_opinions"):
                opinions = self._all(
                    conn,
                    "SELECT target, opinion_type, strength, confidence, reason, evidence_count, updated_at "
                    "FROM agent_opinions WHERE agent_id=? AND status='active' "
                    "ORDER BY confidence*ABS(strength) DESC, updated_at DESC LIMIT 12",
                    (owner_id,),
                )
            self_narrative = None
            if self._table_exists(conn, "memories"):
                rows = self._all(
                    conn,
                    "SELECT content, created_at FROM memories WHERE owner_kind=? AND owner_id=? "
                    "AND memory_type='self_narrative' ORDER BY created_at DESC LIMIT 1",
                    (owner_kind, owner_id),
                )
                if rows:
                    self_narrative = {"content": rows[0].get("content"), "at": rows[0].get("created_at")}
            return {"self_narrative": self_narrative, "opinions": opinions}

    def relationship_notes(self, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """v0.18 牵挂: what the user told her about the user's own life."""
        with self._connect() as conn:
            if not self._table_exists(conn, "relationship_notes"):
                return []
            return self._all(
                conn,
                "SELECT topic, content, salience, sentiment, follow_up_due_ts, followed_up_at, created_at "
                "FROM relationship_notes WHERE agent_id=? AND status='active' "
                "ORDER BY salience DESC, created_at DESC LIMIT ?",
                (owner_id, limit),
            )

    def proactive(self, owner_kind: str, owner_id: str, limit: int = 20) -> dict[str, Any]:
        # proactive tables are keyed by agent_id (proactive is agent-only), not
        # owner_kind/owner_id — query by whichever key the table actually has.
        with self._connect() as conn:
            def _q(table):
                if not self._table_exists(conn, table):
                    return []
                cols = self._columns(conn, table)
                if "agent_id" in cols:
                    return self._all(conn, f"SELECT * FROM {table} WHERE agent_id=? ORDER BY created_at DESC LIMIT ?", (owner_id, limit))
                if "owner_id" in cols:
                    return self._all(conn, f"SELECT * FROM {table} WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))
                return self._all(conn, f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?", (limit,))
            return {"intents": _q("proactive_intents"), "outbox": _q("proactive_outbox")}

    def life_feed(self, owner_kind: str, owner_id: str, before: str | None = None, limit: int = 40) -> dict[str, Any]:
        """A time-ordered narrative of what she actually lived — the "living evidence".

        Merges the engine's authored/lived rows (diary, dreams, serendipity,
        companion outreach, her self-narrative, social rumors, persona drift,
        campaign beats) into one cursor-paginated feed, so the observatory shows
        her day/week as a story instead of only schedule blocks and resource bars.
        `before` is an ISO created_at cursor (returns items strictly older).
        """
        items: list[dict[str, Any]] = []

        def add(row: dict[str, Any], kind: str, icon: str, title: str, text: Any, **meta: Any) -> None:
            ts = row.get("created_at")
            if not ts:
                return
            body = " ".join(str(text or "").split())
            items.append({
                "id": f"{kind}:{row.get('id')}", "ts": ts, "kind": kind, "icon": icon,
                "title": title, "text": body[:400],
                "meta": {k: v for k, v in meta.items() if v is not None},
            })

        with self._connect() as conn:
            cur = " AND created_at < ?" if before else ""
            cp = (before,) if before else ()

            def q(table: str, where: str, params: tuple, order: str = "created_at DESC") -> list[dict[str, Any]]:
                if not self._table_exists(conn, table):
                    return []
                return self._all(conn, f"SELECT * FROM {table} WHERE {where}{cur} ORDER BY {order} LIMIT ?",
                                 (*params, *cp, limit))

            for r in q("diary_entries", "owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                add(r, "diary", "📓", "日记", r.get("content"), diary_type=r.get("diary_type"))
            for r in q("dream_entries", "owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                add(r, "dream", "💭", "梦", r.get("share_text") or r.get("summary") or r.get("content"),
                    symbols=_safe_json(r.get("symbols_json"), []) or None, truth_layer=r.get("truth_layer"))
            for r in q("serendipity_events", "owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                add(r, "serendipity", "🎲", r.get("title") or "偶遇", r.get("description"),
                    serendipity_type=r.get("serendipity_type"))
            for r in q("proactive_intents",
                       "agent_id=? AND intent_type IN ('idle_share','ask_about_user','self_reflection_share')",
                       (owner_id,)):
                add(r, "companion", "📣", "她想跟你说", r.get("summary"),
                    intent_type=r.get("intent_type"), status=r.get("status"))
            for r in q("memories", "owner_kind=? AND owner_id=? AND memory_type='self_narrative'", (owner_kind, owner_id)):
                add(r, "reflection", "🌱", "她的自述", r.get("content"))
            for r in q("rumors", "owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                add(r, "rumor", "🌐", "坊间流言", r.get("content"), truth_layer=r.get("truth_layer"), heat=r.get("heat"))
            for r in q("persona_drift_log", "owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                add(r, "persona", "🎭", f"性格微移 · {r.get('trait_key')}", r.get("reason"),
                    trait=r.get("trait_key"), delta=r.get("delta"))
            if self._table_exists(conn, "campaign_phase_occurrences"):
                crows = self._all(
                    conn,
                    "SELECT o.*, c.title AS campaign_title FROM campaign_phase_occurrences o "
                    "LEFT JOIN campaigns c ON c.id=o.campaign_id "
                    "WHERE o.owner_kind=? AND o.owner_id=?" + (" AND o.created_at < ?" if before else "") +
                    " ORDER BY o.created_at DESC LIMIT ?",
                    (owner_kind, owner_id, *cp, limit),
                )
                for r in crows:
                    add(r, "campaign", "📜", f"资料片 · {r.get('campaign_title') or '事变'}",
                        f"进入「{r.get('phase')}」阶段")

        items.sort(key=lambda x: str(x["ts"]), reverse=True)
        items = items[:limit]
        next_cursor = items[-1]["ts"] if len(items) >= limit else None
        return {"items": items, "next_cursor": next_cursor}

    def collections(self, owner_kind: str, owner_id: str, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            if not self._table_exists(conn, "item_collections"):
                return {"collections": [], "items": [], "outfits": []}
            collections = self._all(conn, "SELECT * FROM item_collections WHERE owner_kind=? AND owner_id=? AND status!='archived' ORDER BY sort_order, created_at LIMIT ?", (owner_kind, owner_id, limit))
            for c in collections:
                for key in ["rules_json", "image_generation_rule_json", "usage_rule_json", "maintenance_rule_json", "required_metadata_json"]:
                    if key in c:
                        c[key.replace("_json", "")] = _safe_json(c.get(key), [] if key == "required_metadata_json" else {})
            items = []
            if self._table_exists(conn, "collection_items"):
                items = self._all(conn, """
                    SELECT i.*, c.name AS collection_name, c.collection_type
                    FROM collection_items i
                    LEFT JOIN item_collections c ON c.id=i.collection_id
                    WHERE i.owner_kind=? AND i.owner_id=? AND i.status!='archived'
                    ORDER BY i.updated_at DESC LIMIT ?
                """, (owner_kind, owner_id, limit))
                alias_by_item = {}
                if self._table_exists(conn, "collection_item_aliases"):
                    for a in self._all(conn, "SELECT item_id, alias FROM collection_item_aliases WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY created_at", (owner_kind, owner_id)):
                        alias_by_item.setdefault(a.get("item_id"), []).append(a.get("alias"))
                asset_by_item = {}
                legacy_asset_uri_by_item = {}
                if self._table_exists(conn, "collection_item_assets"):
                    for a in self._all(conn, "SELECT item_id, status, asset_uri, view_name FROM collection_item_assets WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)):
                        d = asset_by_item.setdefault(a.get("item_id"), {"total":0,"available":0,"pending":0})
                        d["total"] += 1
                        if a.get("status") == "available" and a.get("asset_uri"):
                            d["available"] += 1
                            # Keep first available legacy per-view asset only
                            # as a fallback.  Post-v0.13 collection items use
                            # asset_bundle.display_image/reference_image as the
                            # canonical image pointers; older per-view rows may
                            # point at stale front/side assets and must not win.
                            iid = a.get("item_id")
                            if iid not in legacy_asset_uri_by_item:
                                legacy_asset_uri_by_item[iid] = a.get("asset_uri")
                        else:
                            d["pending"] += 1
                for i in items:
                    for key in ["tags_json", "attributes_json", "material_spec_json", "care_spec_json", "asset_bundle_json", "usage_state_json"]:
                        if key in i:
                            i[key.replace("_json", "")] = _safe_json(i.get(key), [] if key == "tags_json" else {})
                    bundle = i.get("asset_bundle") or {}
                    i["aliases"] = alias_by_item.get(i.get("id"), [])
                    i["asset_counts"] = asset_by_item.get(i.get("id"), {"total":0,"available":0,"pending":0})
                    i["primary_asset_uri"] = (
                        bundle.get("display_image")
                        or bundle.get("reference_image")
                        or legacy_asset_uri_by_item.get(i.get("id"))
                    )
            # Build a collection board grouped by cabinet/drawer/shelf.
            items_by_collection = {}
            for i in items:
                items_by_collection.setdefault(i.get("collection_id"), []).append(i)
            board = []
            for c in collections:
                its = items_by_collection.get(c.get("id"), [])
                board.append({"collection": c, "items": its, "item_count": len(its), "available_count": sum(1 for x in its if x.get("availability_state") == "available"), "needs_asset_count": sum(1 for x in its if (x.get("asset_counts") or {}).get("pending", 0) > 0)})
            loadout = []
            if self._table_exists(conn, "agent_loadout"):
                loadout = self._all(conn, """
                    SELECT l.*, i.description, i.tags_json, i.attributes_json, i.asset_bundle_json, i.usage_state_json,
                           c.name AS collection_name
                    FROM agent_loadout l
                    LEFT JOIN collection_items i ON i.id=l.item_id
                    LEFT JOIN item_collections c ON c.id=l.collection_id
                    WHERE l.owner_kind=? AND l.owner_id=? AND l.status='active'
                    ORDER BY l.slot, l.updated_at DESC
                    LIMIT ?
                """, (owner_kind, owner_id, limit))
                for l in loadout:
                    for key in ["tags_json", "attributes_json", "asset_bundle_json", "usage_state_json"]:
                        if key in l:
                            l[key.replace("_json", "")] = _safe_json(l.get(key), [] if key == "tags_json" else {})
                    bundle = l.get("asset_bundle") or {}
                    l["primary_asset_uri"] = bundle.get("display_image") or bundle.get("reference_image")
            outfits = []
            if self._table_exists(conn, "outfit_plans"):
                outfits = self._all(conn, "SELECT * FROM outfit_plans WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 20", (owner_kind, owner_id))
                for o in outfits:
                    o["item_ids"] = _safe_json(o.get("item_ids_json"), [])
                    o["context"] = _safe_json(o.get("context_json"), {})
            presets = []
            if self._table_exists(conn, "outfit_presets"):
                presets = self._all(conn, "SELECT * FROM outfit_presets WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY updated_at DESC LIMIT 100", (owner_kind, owner_id))
                for p in presets:
                    p["aliases"] = _safe_json(p.get("aliases_json"), [])
                    p["item_refs"] = _safe_json(p.get("item_refs_json"), {})
                    p["context_priority"] = _safe_json(p.get("context_priority_json"), {})
            return {"collections": collections, "items": items, "board": board, "loadout": loadout, "outfits": outfits, "outfit_presets": presets}

    def doctor_latest(self, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
        # The engine persists invariant checks to life_invariant_checks (written by
        # invariants.run_doctor). The old query hit a doctor_runs table that no code
        # path ever creates, so the observatory's health panel was permanently blank.
        # Read the real table; shape it into the {status, summary, issues} the UI expects.
        with self._connect() as conn:
            if not self._table_exists(conn, "life_invariant_checks"):
                return None
            row = self._first(conn, "SELECT * FROM life_invariant_checks WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner_kind, owner_id))
            if not row:
                return None
            checks = _safe_json(row.get("checks_json"), {})
            summary = dict(checks) if isinstance(checks, dict) else {}
            summary["status"] = row.get("status")
            return {
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "summary": summary,
                "issues": _safe_json(row.get("issues_json"), []),
            }

    def trace_latest(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "life_journal"):
                return []
            # life_journal has no source_turn_id/source_tick_id columns; the old
            # query drifted and failed every build, blanking the trace panel. Use
            # the real linkage columns (transaction_id/op_id).
            return self._all(conn, "SELECT id, owner_kind, owner_id, entry_type, source, transaction_id, op_id, created_at FROM life_journal ORDER BY created_at DESC LIMIT ?", (limit,))


    def event_detail(self, event_id: str) -> dict[str, Any]:
        """Return a rich, read-only event explain payload for WebUI drawers."""
        with self._connect() as conn:
            if not self._table_exists(conn, "events"):
                return {"kind": "event", "id": event_id, "found": False}
            event = self._first(conn, "SELECT * FROM events WHERE id=?", (event_id,))
            if not event:
                return {"kind": "event", "id": event_id, "found": False}
            self._decode_event(event)
            owner_kind = str(event.get("owner_kind") or "")
            owner_id = str(event.get("owner_id") or "")
            schedule = []
            schedule_transitions = []
            actions = []
            action_transitions = []
            results = []
            resources = []
            memories = []
            dreams = []
            proactive = []
            journal = []
            execution_sleep_adjustments = []
            if self._table_exists(conn, "schedule_blocks"):
                schedule = self._all(conn, "SELECT * FROM schedule_blocks WHERE event_id=? ORDER BY COALESCE(start_ts, strftime('%s', start)), start", (event_id,))
                for block in schedule:
                    block["interruptibility"] = _safe_json(block.get("interruptibility_json"), {})
            if self._table_exists(conn, "event_state_transitions"):
                transitions = self._all(conn, "SELECT * FROM event_state_transitions WHERE event_id=? ORDER BY COALESCE(occurred_at_ts, strftime('%s', occurred_at)), occurred_at", (event_id,))
                for t in transitions:
                    t["metadata"] = _safe_json(t.get("metadata_json"), {})
            else:
                transitions = []
            if self._table_exists(conn, "schedule_block_state_transitions"):
                schedule_transitions = self._all(conn, "SELECT * FROM schedule_block_state_transitions WHERE event_id=? ORDER BY COALESCE(occurred_at_ts, strftime('%s', occurred_at)), occurred_at", (event_id,))
                for t in schedule_transitions:
                    t["metadata"] = _safe_json(t.get("metadata_json"), {})
            if self._table_exists(conn, "actions"):
                actions = self._all(conn, "SELECT * FROM actions WHERE event_id=? ORDER BY created_at", (event_id,))
            if self._table_exists(conn, "action_state_transitions"):
                action_transitions = self._all(conn, "SELECT * FROM action_state_transitions WHERE event_id=? ORDER BY COALESCE(occurred_at_ts, strftime('%s', occurred_at)), occurred_at", (event_id,))
                for t in action_transitions:
                    t["metadata"] = _safe_json(t.get("metadata_json"), {})
            if self._table_exists(conn, "results"):
                results = self._all(conn, "SELECT * FROM results WHERE event_id=? ORDER BY created_at", (event_id,))
                for r in results:
                    r["state_changes"] = _safe_json(r.get("state_changes_json"), [])
                    r["memory_ids"] = _safe_json(r.get("memory_ids_json"), [])
            if self._table_exists(conn, "resource_ledger"):
                resources = self._all(conn, "SELECT * FROM resource_ledger WHERE event_id=? ORDER BY created_at", (event_id,))
            if self._table_exists(conn, "memories"):
                cols = self._columns(conn, "memories")
                if "event_id" in cols:
                    memories = self._all(conn, "SELECT * FROM memories WHERE event_id=? ORDER BY created_at DESC LIMIT 20", (event_id,))
            if self._table_exists(conn, "dream_entries"):
                cols = self._columns(conn, "dream_entries")
                if "source_event_ids_json" in cols:
                    # JSON membership is SQLite-version dependent; use LIKE as a conservative WebUI hint.
                    dreams = self._all(conn, "SELECT * FROM dream_entries WHERE source_event_ids_json LIKE ? ORDER BY created_at DESC LIMIT 20", (f'%{event_id}%',))
            if self._table_exists(conn, "proactive_intents"):
                cols = self._columns(conn, "proactive_intents")
                if "trigger_event_id" in cols:
                    proactive = self._all(conn, "SELECT * FROM proactive_intents WHERE trigger_event_id=? ORDER BY created_at DESC LIMIT 20", (event_id,))
            if self._table_exists(conn, "execution_sleep_adjustments"):
                execution_sleep_adjustments = self._all(conn, "SELECT * FROM execution_sleep_adjustments WHERE event_id=? ORDER BY created_at DESC LIMIT 20", (event_id,))
                for r in execution_sleep_adjustments:
                    r["sleep_context"] = _safe_json(r.get("sleep_context_json"), {})
                    r["proposed_ops"] = _safe_json(r.get("proposed_ops_json"), [])
            if self._table_exists(conn, "life_journal"):
                journal = self._all(conn, "SELECT id, transaction_id, op_id, entry_type, source, created_at FROM life_journal WHERE owner_kind=? AND owner_id=? AND payload_json LIKE ? ORDER BY created_at DESC LIMIT 30", (owner_kind, owner_id, f'%{event_id}%'))
            return {
                "kind": "event",
                "id": event_id,
                "found": True,
                "event": event,
                "transitions": transitions,
                "schedule_blocks": schedule,
                "schedule_transitions": schedule_transitions,
                "actions": actions,
                "action_transitions": action_transitions,
                "results": results,
                "resource_ledger": resources,
                "memories": memories,
                "dreams": dreams,
                "proactive_intents": proactive,
                "execution_sleep_adjustments": execution_sleep_adjustments,
                "journal": journal,
            }

    def dream_detail(self, dream_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            if not self._table_exists(conn, "dream_entries"):
                return {"kind": "dream", "id": dream_id, "found": False}
            dream = self._first(conn, "SELECT * FROM dream_entries WHERE id=?", (dream_id,))
            if not dream:
                return {"kind": "dream", "id": dream_id, "found": False}
            for key in ["symbols_json", "source_memory_ids_json", "source_event_ids_json", "source_goal_ids_json"]:
                if key in dream:
                    dream[key.replace("_json", "")] = _safe_json(dream.get(key), [])
            runs = []
            findings = []
            journal = []
            if self._table_exists(conn, "dream_runs"):
                cols = self._columns(conn, "dream_runs")
                if "created_entry_id" in cols:
                    runs = self._all(conn, "SELECT * FROM dream_runs WHERE created_entry_id=? ORDER BY started_at DESC LIMIT 10", (dream_id,))
            if runs and self._table_exists(conn, "dream_audit_findings"):
                run_ids = [r.get("id") for r in runs if r.get("id")]
                if run_ids:
                    marks = ",".join("?" for _ in run_ids)
                    findings = self._all(conn, f"SELECT * FROM dream_audit_findings WHERE dream_run_id IN ({marks}) ORDER BY created_at DESC LIMIT 50", tuple(run_ids))
                    for f in findings:
                        f["details"] = _safe_json(f.get("details_json"), {})
                        f["proposed_ops"] = _safe_json(f.get("proposed_ops_json"), [])
            if self._table_exists(conn, "life_journal"):
                journal = self._all(conn, "SELECT id, transaction_id, op_id, entry_type, source, created_at FROM life_journal WHERE payload_json LIKE ? ORDER BY created_at DESC LIMIT 20", (f'%{dream_id}%',))
            return {"kind": "dream", "id": dream_id, "found": True, "dream": dream, "runs": runs, "findings": findings, "journal": journal}

    def trace_explain(self, object_id: str) -> dict[str, Any]:
        """Best-effort explain for journal / transaction / event / dream ids."""
        if not object_id:
            return {"kind": "unknown", "id": object_id, "found": False}
        ev = self.event_detail(object_id)
        if ev.get("found"):
            return ev
        dr = self.dream_detail(object_id)
        if dr.get("found"):
            return dr
        with self._connect() as conn:
            out: dict[str, Any] = {"kind": "trace", "id": object_id, "found": False}
            if self._table_exists(conn, "life_transactions"):
                tx = self._first(conn, "SELECT * FROM life_transactions WHERE id=?", (object_id,))
                if tx:
                    out.update({"kind": "transaction", "found": True, "transaction": tx})
                    if self._table_exists(conn, "life_ops"):
                        ops = self._all(conn, "SELECT * FROM life_ops WHERE transaction_id=? ORDER BY created_at", (object_id,))
                        for op in ops:
                            op["payload"] = _safe_json(op.get("payload_json"), {})
                        out["ops"] = ops
                    if self._table_exists(conn, "commit_receipts"):
                        receipts = self._all(conn, "SELECT * FROM commit_receipts WHERE transaction_id=? ORDER BY created_at", (object_id,))
                        for r in receipts:
                            r["facts"] = _safe_json(r.get("facts_json"), [])
                            r["summary"] = _safe_json(r.get("summary_json"), {})
                        out["receipts"] = receipts
                    if self._table_exists(conn, "life_journal"):
                        out["journal"] = self._all(conn, "SELECT id, entry_type, source, created_at, payload_json FROM life_journal WHERE transaction_id=? ORDER BY created_at", (object_id,))
                    return out
            if self._table_exists(conn, "life_journal"):
                journal = self._first(conn, "SELECT * FROM life_journal WHERE id=?", (object_id,))
                if journal:
                    journal["payload"] = _safe_json(journal.get("payload_json"), {})
                    out.update({"kind": "journal", "found": True, "journal_entry": journal})
                    txid = journal.get("transaction_id")
                    if txid:
                        out["transaction_context"] = self.trace_explain(str(txid))
                    return out
            # Last chance: find references in journal payloads.
            if self._table_exists(conn, "life_journal"):
                refs = self._all(conn, "SELECT id, transaction_id, entry_type, source, created_at FROM life_journal WHERE payload_json LIKE ? ORDER BY created_at DESC LIMIT 30", (f'%{object_id}%',))
                if refs:
                    out.update({"found": True, "references": refs})
            return out


    def workspace_roots(self) -> list[dict[str, Any]]:
        """Return safe workspace roots inferred from the selected LifeEngine DB.

        The WebUI only exposes text/markdown files through explicit read APIs.  It
        does not recursively dump arbitrary workspaces into the live snapshot.
        """
        roots: list[Path] = []
        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()
        roots.append(hermes_home)
        life_dir = self.selection.life_dir.resolve()
        if life_dir.name == "lifeengine":
            roots.append(life_dir.parent)
        roots.append(life_dir)
        try:
            roots.append(Path.cwd().resolve())
        except Exception:
            pass
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in roots:
            try:
                rr = r.resolve()
            except Exception:
                continue
            key = str(rr)
            if key in seen or not rr.exists() or not rr.is_dir():
                continue
            seen.add(key)
            out.append({"label": "Hermes Profile" if rr == hermes_home else rr.name, "path": key})
        return out

    def workspace_docs(self, limit: int = 80, include_content: bool = False) -> dict[str, Any]:
        """List markdown workspace files for the game UI library panel."""
        ignore_dirs = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", "site-packages"}
        wanted_names = {"SOUL.md", "AGENT.md", "AGENTS.md", "agent.md", "agents.md", "README.md", "readme.md"}
        docs: list[dict[str, Any]] = []
        for root_info in self.workspace_roots():
            root = Path(root_info["path"])
            candidates: list[Path] = []
            try:
                for child in sorted(root.iterdir()):
                    if child.name in ignore_dirs:
                        continue
                    if child.is_file() and (child.suffix.lower() == ".md" or child.name in wanted_names):
                        candidates.append(child)
                    elif child.is_dir() and child.name not in ignore_dirs:
                        # One shallow level is enough for agent docs without turning
                        # the WebUI into an accidental filesystem crawler.
                        for sub in sorted(child.iterdir()):
                            if sub.is_file() and (sub.suffix.lower() == ".md" or sub.name in wanted_names):
                                candidates.append(sub)
            except Exception:
                continue
            for f in candidates:
                try:
                    st = f.stat()
                    rel = str(f.relative_to(root))
                    item = {
                        "root_label": root_info["label"],
                        "root_path": str(root),
                        "relative_path": rel,
                        "path": str(f.resolve()),
                        "name": f.name,
                        "size_bytes": st.st_size,
                        "modified_at": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(),
                    }
                    if include_content and st.st_size <= 120_000:
                        text = f.read_text(encoding="utf-8", errors="replace")
                        item["content"] = text[:60000]
                    docs.append(item)
                    if len(docs) >= limit:
                        return {"roots": self.workspace_roots(), "docs": docs}
                except Exception:
                    continue
        return {"roots": self.workspace_roots(), "docs": docs}

    def workspace_file(self, path: str) -> dict[str, Any]:
        """Read one safe text file from a known workspace root."""
        requested = Path(path).expanduser().resolve()
        allowed_suffixes = {".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini"}
        roots = [Path(r["path"]).resolve() for r in self.workspace_roots()]
        if not any(str(requested).startswith(str(root) + os.sep) or requested == root for root in roots):
            raise PermissionError("文件不在已选择的 Hermes/LifeEngine 工作区内。")
        if requested.suffix.lower() not in allowed_suffixes:
            raise PermissionError("WebUI 只读取 markdown/text/config 类文本文件。")
        st = requested.stat()
        if st.st_size > 240_000:
            raise ValueError("文件过大，WebUI 只预览 240KB 以下文本。")
        text = requested.read_text(encoding="utf-8", errors="replace")
        return {
            "path": str(requested),
            "name": requested.name,
            "size_bytes": st.st_size,
            "modified_at": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(),
            "content": text,
        }

    def _life_feed_head(self, owner_kind: str, owner_id: str) -> str | None:
        """Newest-life-content signal for the snapshot hash: the max created_at
        across the narrative tables. Bumping it lets the SSE fire (and the
        LifeFeed live-refresh) whenever she lives something new."""
        heads: list[str] = []
        with self._connect() as conn:
            for table, agent_keyed in (
                ("diary_entries", False), ("dream_entries", False), ("serendipity_events", False),
                ("memories", False), ("rumors", False), ("persona_drift_log", False),
                ("proactive_intents", True),
            ):
                if not self._table_exists(conn, table):
                    continue
                cols = self._columns(conn, table)
                if "created_at" not in cols:
                    continue
                if agent_keyed and "agent_id" in cols:
                    row = self._first(conn, f"SELECT MAX(created_at) AS m FROM {table} WHERE agent_id=?", (owner_id,))
                elif "owner_id" in cols:
                    row = self._first(conn, f"SELECT MAX(created_at) AS m FROM {table} WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
                else:
                    continue
                if row and row.get("m"):
                    heads.append(str(row["m"]))
        return max(heads) if heads else None

    def snapshot(self, owner_kind: str, owner_id: str, period: str = "today", date: str | None = None) -> dict[str, Any]:
        state = self.realtime_state(owner_kind, owner_id)
        current = self.current_event(owner_kind, owner_id, state)
        sleep_day = self.latest_sleep_day(owner_kind, owner_id)
        schedule = self.schedule(owner_kind, owner_id, period=period, date=date, limit=120)
        review = self.review_items(owner_kind, owner_id, limit=50)
        resources = self.resources(owner_kind, owner_id)
        dreams = self.dreams(owner_kind, owner_id, limit=8)
        delayed = self.delayed_replies(owner_kind, owner_id, limit=20)
        pro = self.proactive(owner_kind, owner_id, limit=10)
        collections = self.collections(owner_kind, owner_id, limit=50)
        campaigns = self.campaigns(owner_kind, owner_id, limit=12)
        inner_life = self.inner_life(owner_kind, owner_id)
        relationship = self.relationship_notes(owner_kind, owner_id, limit=20)
        identity = self.identity(owner_kind, owner_id)
        world_model = self.world_model(
            owner_kind, owner_id, limit=80,
            current_location=(current or {}).get("location") if current else None,
            actor_label=identity.get("name") or "明灯",
        )
        social_world = self.social_world(owner_kind, owner_id, limit=80)
        sprite = map_avatar_state(state, current, sleep_day, review, delayed)
        workspace = self.workspace_docs(limit=20, include_content=False)
        payload = {
            "meta": self.meta(),
            "owners": self.owners(),
            "owner": {"owner_kind": owner_kind, "owner_id": owner_id},
            "identity": identity,
            "control": self.control(owner_kind, owner_id),
            "state": state,
            "current_event": current,
            "sleep_day_state": sleep_day,
            "schedule": schedule,
            "review_items": review,
            "resources": resources,
            "dreams": dreams,
            "delayed_replies": delayed,
            "proactive": pro,
            "collections": collections,
            "campaigns": campaigns,
            "inner_life": inner_life,
            "relationship": relationship,
            "world_model": world_model,
            "social_world": social_world,
            "workspace": workspace,
            "doctor": self.doctor_latest(owner_kind, owner_id),
            "persona": self.persona(owner_kind, owner_id),
            "meals_today": self.meals_today(owner_kind, owner_id),
            "clock": self.clock(owner_kind, owner_id),
            "recent_events": self.events(owner_kind, owner_id, limit=30),
            "trace": self.trace_latest(limit=15),
            "avatar": sprite,
            "life_feed_head": self._life_feed_head(owner_kind, owner_id),
        }
        # Hash the substantive payload only, excluding fields that change on every
        # 2s rebuild even when nothing the human cares about changed. A churning
        # hash defeats SSE de-duplication and forces a full client re-render each
        # tick (forms cleared, panels re-collapsed) — the root cause of the page
        # "twitching instead of breathing". Excluded:
        #   - updated_at: the build wall-clock (added back AFTER hashing);
        #   - clock.iso / clock.hour: sub-minute live time. The minute-granularity
        #     clock parts (hhmm/phase/label) stay IN the digest so a real
        #     day->night transition still pushes.
        hash_input = dict(payload)
        stable_clock = dict(payload.get("clock") or {})
        stable_clock.pop("iso", None)
        stable_clock.pop("hour", None)
        hash_input["clock"] = stable_clock
        payload["snapshot_hash"] = hashlib.sha256(json.dumps(_jsonable(hash_input), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        payload["updated_at"] = _now().isoformat()
        return payload


def _jsonable(v: Any) -> Any:
    if isinstance(v, (_dt.datetime, Path)):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def period_range(period: str = "today", date: str | None = None) -> tuple[_dt.datetime, _dt.datetime, str]:
    now = _dt.datetime.now().astimezone()
    period = (period or "today").lower()
    if date:
        day = _dt.date.fromisoformat(date)
    elif period in {"day"}:
        day = now.date()
    elif period in {"tomorrow", "明天"}:
        day = now.date() + _dt.timedelta(days=1)
    elif period in {"yesterday", "昨天"}:
        day = now.date() - _dt.timedelta(days=1)
    elif period and len(period) >= 10 and period[:4].isdigit():
        day = _dt.date.fromisoformat(period[:10])
        period = "day"
    else:
        day = now.date()
    tz = now.tzinfo
    if period in {"week", "this_week", "本周", "周"}:
        start_day = day - _dt.timedelta(days=day.weekday())
        start = _dt.datetime.combine(start_day, _dt.time.min, tzinfo=tz)
        end = start + _dt.timedelta(days=7)
        label = f"本周 {start_day.isoformat()} - {(end.date() - _dt.timedelta(days=1)).isoformat()}"
    else:
        start = _dt.datetime.combine(day, _dt.time.min, tzinfo=tz)
        end = start + _dt.timedelta(days=1)
        if period in {"tomorrow", "明天"}:
            label = f"明天 {day.isoformat()}"
        elif period in {"yesterday", "昨天"}:
            label = f"昨天 {day.isoformat()}"
        else:
            label = f"今天 {day.isoformat()}" if day == now.date() else day.isoformat()
    return start, end, label


def map_avatar_state(state: dict[str, Any], current_event: dict[str, Any] | None, sleep_day: dict[str, Any] | None, review: list[dict[str, Any]], delayed: list[dict[str, Any]]) -> dict[str, Any]:
    mode = (state or {}).get("mode") or "unknown"
    body = (state or {}).get("body_state") or _safe_json((state or {}).get("body_state_json"), {}) or {}
    category = (current_event or {}).get("event_category") or (current_event or {}).get("event_type") or ""
    activity = (current_event or {}).get("activity_domain") or ""
    recovery_pressure = int((sleep_day or {}).get("recovery_pressure") or body.get("recovery_pressure") or 0)
    fatigue = int(body.get("fatigue") or (sleep_day or {}).get("fatigue_delta") or 0)
    if mode in {"asleep", "napping"}:
        sprite, label, bubble = "sleep", "睡觉中", "Zzz…"
    elif mode == "dreaming":
        sprite, label, bubble = "dream", "做梦中", "梦境整理中"
    elif mode in {"waiting_to_reply"} or delayed:
        sprite, label, bubble = "reply", "待回复", f"有 {len(delayed)} 条延迟消息"
    elif mode in {"uninterruptible_event"}:
        sprite, label, bubble = "battle", "不可打断事件", "忙碌中"
    elif category in {"work", "study", "creative", "maintenance"} or activity in {"craft_commission", "fieldwork"}:
        sprite, label, bubble = "work", "工作/学习中", (current_event or {}).get("title") or "推进任务"
    elif category in {"health", "fitness", "travel"}:
        sprite, label, bubble = "walk", "行动中", (current_event or {}).get("title") or "外出/活动"
    elif category in {"meal"}:
        sprite, label, bubble = "eat", "吃饭中", "补充能量"
    elif current_event:
        sprite, label, bubble = "work", "进行中", (current_event or {}).get("title") or "推进事项"
    elif fatigue >= 75:
        sprite, label, bubble = "tired", "疲惫不堪", "需要休息"
    elif recovery_pressure >= 70:
        sprite, label, bubble = "recover", "恢复中", "正在缓一缓、回血"
    elif mode in {"busy", "in_conversation"}:
        sprite, label, bubble = "idle", "闲置", "暂无日程"
    else:
        sprite, label, bubble = "idle", "待机", "观察生活流"
    return {"sprite_state": sprite, "label": label, "bubble": bubble, "mode": mode, "scene": scene_for(sprite)}


def scene_for(sprite: str) -> str:
    return {
        "sleep": "night_room",
        "dream": "dream_space",
        "reply": "message_room",
        "battle": "combat_alley",
        "work": "workshop",
        "walk": "city_walk",
        "eat": "meal_corner",
        "tired": "recovery_room",
        "recover": "recovery_room",
        "idle": "observatory",
    }.get(sprite, "observatory")
