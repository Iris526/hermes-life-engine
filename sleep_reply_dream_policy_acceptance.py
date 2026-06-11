"""Sleep/Reply/Dream policy acceptance and conflict validation for v0.11.11."""
from __future__ import annotations

from typing import Any, Callable

from .constants import PLUGIN_VERSION
from .db import _SCHEMA_VERSION
from .jsonutil import dumps, loads
from .trace import append_audit, new_id
from .sleep_reply_dream_policy import validate_policy


def _version_at_least(current: str, minimum: str) -> bool:
    def parts(v: str):
        return tuple(int(x) for x in str(v).split(".") if x.isdigit())
    return parts(current) >= parts(minimum)


SCENARIOS: list[tuple[str, str]] = [
    ("POL01_PRESETS_VALIDATE", "All built-in SRD policy presets validate without hard conflicts"),
    ("POL02_NIGHT_OWL_AFFECTS_SLEEP_PLAN", "night_owl preset changes default core sleep plan timing"),
    ("POL03_PRIVATE_DREAM_NO_SHARE", "private preset disables wake dream sharing"),
    ("POL04_GENTLE_NAP_THRESHOLD", "gentle preset lowers recovery nap threshold"),
    ("POL05_CONFLICT_DETECTION", "invalid custom policy emits conflict report"),
    ("POL06_EXPORT_IMPORT_ROUNDTRIP", "policy export/import round-trips into another owner"),
]


def _safe(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        out = fn()
        out.setdefault("ok", True)
        return out
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _fact(out: dict[str, Any], key: str) -> str:
    for fact in (out.get("receipt") or {}).get("facts", []):
        ev = fact.get("evidence") or {}
        if ev.get(key):
            return ev[key]
    raise AssertionError(f"{key} not found in receipt")


def run_sleep_reply_dream_policy_acceptance(rt: Any, owner_kind: str, owner_id: str) -> dict[str, Any]:
    run_id = new_id("srdpolicyacc")
    synthetic_owner_id = f"{owner_id}-pol-{run_id}"
    # Keep acceptance state under synthetic owner so real policy is untouched.
    rt.setup("v0.11.11 policy acceptance synthetic agent", owner_id=synthetic_owner_id)
    rt.commit_canon(owner_id=synthetic_owner_id)
    rt.control("resume", owner_id=synthetic_owner_id)

    base_checks = [
        {"name": "schema_version", "ok": _SCHEMA_VERSION >= 31, "value": _SCHEMA_VERSION},
        {"name": "plugin_version", "ok": _version_at_least(PLUGIN_VERSION, "0.11.11"), "value": PLUGIN_VERSION},
    ]
    context: dict[str, Any] = {"synthetic_owner_id": synthetic_owner_id}

    def s1_presets_validate() -> dict[str, Any]:
        results = []
        for preset in ["balanced", "gentle", "night_owl", "workday", "private", "debug"]:
            rt.policy("preset", owner_id=synthetic_owner_id, preset=preset)
            out = rt.policy("conflicts", owner_id=synthetic_owner_id)
            results.append({"preset": preset, "ok": out["validation"]["ok"], "conflicts": out["validation"]["conflict_count"]})
        assert all(r["ok"] for r in results), results
        return {"ok": True, "results": results}

    def s2_night_owl_sleep_plan() -> dict[str, Any]:
        rt.policy("preset", owner_id=synthetic_owner_id, preset="night_owl")
        plan = rt.sleep_tool("plan_day", owner_id=synthetic_owner_id, date="2026-06-10", timezone="UTC")
        plan_id = _fact(plan, "sleep_plan_id")
        row = rt.sleep_tool("get_plan", owner_id=synthetic_owner_id, sleep_plan_id=plan_id)["sleep_plan"]
        assert "00:30" in row.get("planned_sleep_at", ""), row
        context["night_owl_plan_id"] = plan_id
        return {"ok": True, "sleep_plan_id": plan_id, "planned_sleep_at": row.get("planned_sleep_at")}

    def s3_private_no_dream_share() -> dict[str, Any]:
        rt.policy("preset", owner_id=synthetic_owner_id, preset="private")
        pol = rt.policy("get", owner_id=synthetic_owner_id)["policy"]["effective_policy"]
        assert pol["dream"]["share_on_wake"] is False
        assert pol["dream"]["share_mode"] == "self_journal"
        return {"ok": True, "dream": pol["dream"]}

    def s4_gentle_nap_threshold() -> dict[str, Any]:
        rt.policy("preset", owner_id=synthetic_owner_id, preset="gentle")
        pol = rt.policy("get", owner_id=synthetic_owner_id)["policy"]["effective_policy"]
        assert int(pol["sleep"]["nap"]["trigger_recovery_pressure"]) <= 50
        return {"ok": True, "nap": pol["sleep"]["nap"]}

    def s5_conflict_detection() -> dict[str, Any]:
        rt.policy("set", owner_id=synthetic_owner_id, policy_patch={"sleep": {"target_sleep_minutes": 0}, "reply": {"gate_mode": "strict", "call_words": []}})
        out = rt.policy("conflicts", owner_id=synthetic_owner_id)
        assert out["validation"]["conflict_count"] >= 2, out
        context["conflict_report_id"] = out["validation"].get("report_id")
        return {"ok": True, "conflict_report_id": out["validation"].get("report_id"), "conflict_count": out["validation"]["conflict_count"]}

    def s6_export_import_roundtrip() -> dict[str, Any]:
        # Reset to a valid policy before export.
        rt.policy("preset", owner_id=synthetic_owner_id, preset="workday")
        exp = rt.policy("export", owner_id=synthetic_owner_id)
        target_owner = f"{synthetic_owner_id}-imported"
        imp = rt.policy("import", owner_id=target_owner, path=exp["path"], apply=True)
        imported = rt.policy("get", owner_id=target_owner)["policy"]["effective_policy"]
        assert imported["profile"] == "workday", imported
        return {"ok": True, "export_id": exp["export_id"], "import_id": imp["import_id"], "target_owner": target_owner, "profile": imported["profile"]}

    scenario_fns: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
        (*SCENARIOS[0], s1_presets_validate),
        (*SCENARIOS[1], s2_night_owl_sleep_plan),
        (*SCENARIOS[2], s3_private_no_dream_share),
        (*SCENARIOS[3], s4_gentle_nap_threshold),
        (*SCENARIOS[4], s5_conflict_detection),
        (*SCENARIOS[5], s6_export_import_roundtrip),
    ]
    results = []
    passed = failed = 0
    for key, desc, fn in scenario_fns:
        out = _safe(fn)
        status = "passed" if out.get("ok") else "failed"
        passed += 1 if status == "passed" else 0
        failed += 1 if status == "failed" else 0
        sid = new_id("srdpolicyaccscenario")
        rt.conn.execute(
            """INSERT INTO sleep_reply_dream_policy_acceptance_scenarios(
                 id, acceptance_run_id, scenario_key, description, status, details_json, error
               ) VALUES(?,?,?,?,?,?,?)""",
            (sid, run_id, key, desc, status, dumps(out), out.get("error")),
        )
        results.append({"key": key, "description": desc, "status": status, "details": out})
    status = "passed" if failed == 0 and all(c["ok"] for c in base_checks) else "failed"
    rt.conn.execute(
        """INSERT INTO sleep_reply_dream_policy_acceptance_runs(
             id, owner_kind, owner_id, synthetic_owner_id, status, passed, failed, checks_json, output_json, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (run_id, owner_kind, owner_id, synthetic_owner_id, status, passed, failed, dumps(base_checks), dumps({"scenarios": results, "context": context})),
    )
    append_audit(rt.conn, owner_kind, owner_id, "sleep_reply_dream_policy_acceptance", status, "Sleep/Reply/Dream policy acceptance", {"acceptance_run_id": run_id, "passed": passed, "failed": failed, "synthetic_owner_id": synthetic_owner_id})
    return {"ok": status == "passed", "acceptance_run_id": run_id, "synthetic_owner_id": synthetic_owner_id, "status": status, "passed": passed, "failed": failed, "checks": base_checks, "scenarios": results, "context": context}


def list_sleep_reply_dream_policy_acceptance(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_acceptance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["checks"] = loads(d.pop("checks_json"), [])
        d["output"] = loads(d.pop("output_json"), {})
        out.append(d)
    return out


def get_sleep_reply_dream_policy_acceptance(conn, acceptance_run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sleep_reply_dream_policy_acceptance_runs WHERE id=?", (acceptance_run_id,)).fetchone()
    if not row:
        raise ValueError(f"acceptance run not found: {acceptance_run_id}")
    d = dict(row)
    d["checks"] = loads(d.pop("checks_json"), [])
    d["output"] = loads(d.pop("output_json"), {})
    scenarios = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_acceptance_scenarios WHERE acceptance_run_id=? ORDER BY created_at ASC",
        (acceptance_run_id,),
    ).fetchall()
    d["scenarios"] = []
    for s in scenarios:
        sd = dict(s)
        sd["details"] = loads(sd.pop("details_json"), {})
        d["scenarios"].append(sd)
    return d
