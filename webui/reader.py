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
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


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
        except sqlite3.Error:
            return None

    def _all(self, conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error:
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
            for table in ["engine_control", "events", "schedule_blocks", "agent_realtime_state", "resource_accounts", "memories"]:
                if self._table_exists(conn, table):
                    try:
                        for r in conn.execute(f"SELECT DISTINCT owner_kind, owner_id FROM {table} WHERE owner_kind IS NOT NULL AND owner_id IS NOT NULL LIMIT 200"):
                            owners.add((str(r[0]), str(r[1])))
                    except sqlite3.Error:
                        pass
        if not owners:
            owners.add(("agent", "default-agent"))
        return [{"owner_kind": k, "owner_id": v} for k, v in sorted(owners)]

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
            return self._all(conn, """
                SELECT a.resource_key, a.current_value, a.unit, a.capacity, a.state, d.display_name, d.resource_class, d.min_value, d.max_value
                FROM resource_accounts a
                LEFT JOIN resource_definitions d
                  ON d.owner_kind=a.owner_kind AND d.owner_id=a.owner_id AND d.key=a.resource_key
                WHERE a.owner_kind=? AND a.owner_id=?
                ORDER BY a.resource_key
            """, (owner_kind, owner_id))

    def current_event(self, owner_kind: str, owner_id: str, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
        state = state or self.realtime_state(owner_kind, owner_id)
        event_id = state.get("active_event_id")
        with self._connect() as conn:
            if not event_id and state.get("active_schedule_block_id") and self._table_exists(conn, "schedule_blocks"):
                block = self._first(conn, "SELECT event_id FROM schedule_blocks WHERE id=?", (state.get("active_schedule_block_id"),))
                event_id = (block or {}).get("event_id")
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
            meals = []
            for mt, hhmm in sorted(times.items(), key=lambda kv: order.get(kv[0], 9)):
                rec = by_type.get(mt)
                meals.append({"meal_type": mt, "time": hhmm,
                              "status": (rec.get("status") if rec else None) or ("pending" if not rec else "eaten"),
                              "skip_reason": rec.get("skip_reason") if rec else None})
            return {"date": date_key, "meals": meals}

    def persona(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        """Living-persona traits (v0.14.0) for the HUD — read-only."""
        with self._connect() as conn:
            if not self._table_exists(conn, "persona_traits"):
                return {"seeded": False, "traits": []}
            rows = self._all(conn, "SELECT trait_key, value, baseline, evidence_count FROM persona_traits WHERE owner_kind=? AND owner_id=? ORDER BY trait_key", (owner_kind, owner_id))
            if not rows:
                return {"seeded": False, "traits": []}
            traits = []
            notable = []
            for r in rows:
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
            for r in rows:
                r["action_hint"] = _safe_json(r.get("action_hint_json"), {})
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
            return self._all(conn, "SELECT * FROM delayed_replies WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))

    def dreams(self, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "dream_entries"):
                return []
            rows = self._all(conn, "SELECT * FROM dream_entries WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))
            for r in rows:
                r["symbols"] = _safe_json(r.get("symbols_json"), [])
            return rows

    def proactive(self, owner_kind: str, owner_id: str, limit: int = 20) -> dict[str, Any]:
        with self._connect() as conn:
            intents = []
            outbox = []
            if self._table_exists(conn, "proactive_intents"):
                intents = self._all(conn, "SELECT * FROM proactive_intents WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))
            if self._table_exists(conn, "proactive_outbox"):
                outbox = self._all(conn, "SELECT * FROM proactive_outbox WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, limit))
            return {"intents": intents, "outbox": outbox}

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
        with self._connect() as conn:
            if not self._table_exists(conn, "doctor_runs"):
                return None
            row = self._first(conn, "SELECT * FROM doctor_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1", (owner_kind, owner_id))
            if row:
                row["summary"] = _safe_json(row.get("summary_json"), {})
            return row

    def trace_latest(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._table_exists(conn, "life_journal"):
                return []
            return self._all(conn, "SELECT id, owner_kind, owner_id, entry_type, source, source_turn_id, source_tick_id, created_at FROM life_journal ORDER BY created_at DESC LIMIT ?", (limit,))


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
        sprite = map_avatar_state(state, current, sleep_day, review, delayed)
        workspace = self.workspace_docs(limit=20, include_content=False)
        payload = {
            "meta": self.meta(),
            "owners": self.owners(),
            "owner": {"owner_kind": owner_kind, "owner_id": owner_id},
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
            "workspace": workspace,
            "doctor": self.doctor_latest(owner_kind, owner_id),
            "persona": self.persona(owner_kind, owner_id),
            "meals_today": self.meals_today(owner_kind, owner_id),
            "clock": self.clock(owner_kind, owner_id),
            "recent_events": self.events(owner_kind, owner_id, limit=30),
            "trace": self.trace_latest(limit=15),
            "avatar": sprite,
            "updated_at": _now().isoformat(),
        }
        payload["snapshot_hash"] = hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
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
    elif recovery_pressure >= 70 or fatigue >= 75:
        sprite, label, bubble = "tired", "疲惫 / 需要恢复", "需要休息"
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
        "idle": "observatory",
    }.get(sprite, "observatory")
