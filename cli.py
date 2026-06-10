"""Slash command and CLI command support for Hermes LifeEngine."""

from __future__ import annotations

import argparse
import json
import shlex
from typing import Any

from .heartbeat import install_heartbeat_cron, heartbeat_installation_status, run_tick_script_once, write_tick_script
from .runtime import LifeEngineRuntime, format_result


def setup_cli_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="lifeengine_action")

    sub.add_parser("status", help="Show LifeEngine status")

    p_doctor = sub.add_parser("doctor", help="Run LifeEngine health checks")
    p_doctor.add_argument("--level", choices=["quick", "full"], default="full")
    p_doctor.add_argument("--include-samples", action="store_true")

    p_upgrade = sub.add_parser("upgrade", help="Install/upgrade/backup/maintenance operations")
    p_upgrade.add_argument(
        "action", nargs="?", default="check",
        choices=[
            "check", "status", "history", "backup", "backups",
            "rebuild_memory", "rebuild_indexes", "verify_memory",
            "export", "exports", "inspect_export", "import", "restore",
            "package_check", "large_smoke", "maintenance", "cron_test",
            "release_check", "all",
            "surface", "surface_check", "integration_check", "integration_smoke", "integration_acceptance", "hermes_integration",
            "api_freeze", "api_freeze_snapshot", "freeze_snapshot", "api_freeze_status", "freeze_status",
            "release_readiness", "v1_rc_check", "v1_rc_acceptance", "mandatory_gate_patch", "core_patch", "core_patch_draft", "core_patches", "patches",
            "concurrency_smoke", "schedule_overlap_smoke",
            "heartbeat_idempotency_smoke", "lifeops_stress",
            "acceptance", "acceptance_suite", "v1_rc_acceptance",
            "acceptance_reports", "acceptance_report", "acceptance_runs",
            "v1_rc_checklists",
        ],
    )
    p_upgrade.add_argument("--include-details", action="store_true")
    p_upgrade.add_argument("--no-audit", action="store_true")
    p_upgrade.add_argument("--reason", default="manual CLI")
    p_upgrade.add_argument("--destination")
    p_upgrade.add_argument("--script-path")
    p_upgrade.add_argument("--archive-path")
    p_upgrade.add_argument("--root")
    p_upgrade.add_argument("--memories", type=int, default=250)
    p_upgrade.add_argument("--timeout", type=int, default=30)
    p_upgrade.add_argument("--limit", type=int, default=20)
    p_upgrade.add_argument("--workers", type=int, default=4)
    p_upgrade.add_argument("--items", type=int, default=100)
    p_upgrade.add_argument("--report-id")
    p_upgrade.add_argument("--acceptance-run-id")
    p_upgrade.add_argument("--report-path")

    p_setup = sub.add_parser("setup", help="Enter setup mode or append a setup statement")
    p_setup.add_argument("text", nargs="*", help="Natural-language setup statement")

    p_commit = sub.add_parser("commit", help="Commit current CanonDraft")
    p_commit.add_argument("--no-activate", action="store_true")

    p_branch = sub.add_parser("branch", help="Create a Life branch marker")
    p_branch.add_argument("name")

    p_control = sub.add_parser("control", help="Set engine control state")
    p_control.add_argument("action", choices=["setup", "pause", "resume", "disable", "readonly"])
    p_control.add_argument("--reason", default="")

    p_module = sub.add_parser("module", help="Set a module gate")
    p_module.add_argument("key")
    p_module.add_argument("value")

    p_heartbeat = sub.add_parser("heartbeat", help="Set heartbeat mode or install Hermes cron script")
    p_heartbeat.add_argument("mode", choices=["off", "manual", "hermes_cron", "embedded_thread", "framework_driver", "install", "status", "run-script", "test"])
    p_heartbeat.add_argument("--schedule", default="every 5m")
    p_heartbeat.add_argument("--deliver", default="local")
    p_heartbeat.add_argument("--name", default="lifeengine-heartbeat")
    p_heartbeat.add_argument("--run", action="store_true")

    p_tick = sub.add_parser("tick", help="Run a manual heartbeat tick")
    p_tick.add_argument("--now", default=None)

    p_resource = sub.add_parser("resource", help="Scalar resource operations")
    p_resource.add_argument("action", choices=["list", "define", "delta", "reserve", "release", "reconcile"])
    p_resource.add_argument("--key")
    p_resource.add_argument("--display-name")
    p_resource.add_argument("--class", dest="resource_class", default="capacity")
    p_resource.add_argument("--unit", default="points")
    p_resource.add_argument("--initial", type=float, default=0)
    p_resource.add_argument("--delta", type=float)
    p_resource.add_argument("--operation", default="adjust")
    p_resource.add_argument("--reason", default="manual CLI")
    p_resource.add_argument("--amount", type=float)
    p_resource.add_argument("--reservation-id")

    p_inventory = sub.add_parser("inventory", help="Inventory/entity resource operations")
    p_inventory.add_argument("action", choices=["list", "add", "create", "update", "delta", "consume", "discard", "move", "movements", "meal", "meals"])
    p_inventory.add_argument("--item-id")
    p_inventory.add_argument("--name")
    p_inventory.add_argument("--category")
    p_inventory.add_argument("--quantity", type=float)
    p_inventory.add_argument("--quantity-delta", type=float)
    p_inventory.add_argument("--unit")
    p_inventory.add_argument("--condition")
    p_inventory.add_argument("--location")
    p_inventory.add_argument("--from-location")
    p_inventory.add_argument("--to-location")
    p_inventory.add_argument("--reason", default="manual CLI")
    p_inventory.add_argument("--meal-type")
    p_inventory.add_argument("--eaten-at")
    p_inventory.add_argument("--food", action="append", default=[])
    p_inventory.add_argument("--notes")
    p_inventory.add_argument("--source", default="manual_entry")
    p_inventory.add_argument("--limit", type=int, default=30)


    p_goal = sub.add_parser("goal", help="Goals, life arcs, and event decomposition")
    p_goal.add_argument("action", choices=["list", "create", "add", "progress", "update_progress", "arc", "create_arc", "arcs", "link_event", "link", "dependency", "add_dependency", "decompose", "decompose_event", "reflect", "reflection", "reflections"])
    p_goal.add_argument("--goal-id")
    p_goal.add_argument("--arc-id")
    p_goal.add_argument("--event-id")
    p_goal.add_argument("--parent-event-id")
    p_goal.add_argument("--child-event-id")
    p_goal.add_argument("--depends-on-event-id")
    p_goal.add_argument("--title")
    p_goal.add_argument("--description")
    p_goal.add_argument("--goal-type", default="lifestyle")
    p_goal.add_argument("--arc-type", default="lifestyle")
    p_goal.add_argument("--stage")
    p_goal.add_argument("--status")
    p_goal.add_argument("--priority", type=int)
    p_goal.add_argument("--progress", type=float)
    p_goal.add_argument("--progress-delta", type=float)
    p_goal.add_argument("--target-date")
    p_goal.add_argument("--metrics", default="{}")
    p_goal.add_argument("--summary", default="{}")
    p_goal.add_argument("--role", default="contributes")
    p_goal.add_argument("--weight", type=float, default=1.0)
    p_goal.add_argument("--dependency-type", default="subevent")
    p_goal.add_argument("--children", default="[]", help="JSON list for decompose")
    p_goal.add_argument("--reason")
    p_goal.add_argument("--limit", type=int, default=20)

    p_autonomy = sub.add_parser("autonomy", help="Autonomy Planner operations")
    p_autonomy.add_argument("action", choices=["list", "get", "plan", "run"])
    p_autonomy.add_argument("--decision-id")
    p_autonomy.add_argument("--now", default=None)
    p_autonomy.add_argument("--manual", action="store_true")
    p_autonomy.add_argument("--limit", type=int, default=20)

    p_proactive = sub.add_parser("proactive", help="Proactive intent and outbox operations")
    p_proactive.add_argument("action", choices=["list", "get", "create", "evaluate", "outbox", "send", "suppress", "expire", "state"])
    p_proactive.add_argument("--intent-id")
    p_proactive.add_argument("--outbox-id")
    p_proactive.add_argument("--target-user-id")
    p_proactive.add_argument("--target-type", default="self_journal")
    p_proactive.add_argument("--target-id")
    p_proactive.add_argument("--intent-type", default="share_interesting")
    p_proactive.add_argument("--summary")
    p_proactive.add_argument("--draft-text")
    p_proactive.add_argument("--importance", type=int, default=50)
    p_proactive.add_argument("--urgency", type=int, default=50)
    p_proactive.add_argument("--novelty", type=int, default=50)
    p_proactive.add_argument("--relationship-relevance", type=int, default=50)
    p_proactive.add_argument("--privacy-level", default="safe_to_share")
    p_proactive.add_argument("--manual", action="store_true")
    p_proactive.add_argument("--reason", default="manual CLI")
    p_proactive.add_argument("--status")
    p_proactive.add_argument("--limit", type=int, default=20)


    p_execution = sub.add_parser("execution", help="Narrative execution simulator and serendipity")
    p_execution.add_argument("action", choices=["list", "decisions", "get", "run", "simulate", "execute", "serendipity"])
    p_execution.add_argument("--decision-id")
    p_execution.add_argument("--schedule-block-id")
    p_execution.add_argument("--now", default=None)
    p_execution.add_argument("--limit", type=int, default=20)

    p_confirm = sub.add_parser("confirmation", help="User Life confirmation flow")
    p_confirm.add_argument("action", choices=["list", "get", "propose", "confirm", "reject"])
    p_confirm.add_argument("--confirmation-id")
    p_confirm.add_argument("--ops", default="[]", help="JSON LifeOps for propose")
    p_confirm.add_argument("--reason", default="requires user confirmation")
    p_confirm.add_argument("--note", default="")
    p_confirm.add_argument("--status", default="pending")
    p_confirm.add_argument("--limit", type=int, default=20)

    p_truth = sub.add_parser("truth", help="TruthSource operations")
    p_truth.add_argument("action", choices=["list", "resolve", "observe", "bind"])
    p_truth.add_argument("--domain")
    p_truth.add_argument("--authority")
    p_truth.add_argument("--value")
    p_truth.add_argument("--parameters", default="{}", help="JSON object")
    p_truth.add_argument("--result", default="{}", help="JSON object for observe")
    p_truth.add_argument("--source", default="tool_observation")
    p_truth.add_argument("--ttl", type=int, default=None)
    p_truth.add_argument("--fallback")
    p_truth.add_argument("--allow-stale", action="store_true")
    p_truth.add_argument("--limit", type=int, default=10)

    p_final = sub.add_parser("final_gate", help="FinalGate claim/evidence reports and repair suggestions")
    p_final.add_argument("action", choices=["check", "audit", "simulate", "reports", "list", "get", "explain"])
    p_final.add_argument("--response-text", "--text", dest="response_text", default="")
    p_final.add_argument("--session-id")
    p_final.add_argument("--turn-id")
    p_final.add_argument("--mode", default=None)
    p_final.add_argument("--no-report", action="store_true")
    p_final.add_argument("--report-id")
    p_final.add_argument("--limit", type=int, default=20)

    p_trace = sub.add_parser("trace", help="Trace operations")
    p_trace.add_argument("action", nargs="?", default="latest", choices=["latest", "audit", "explain", "verify", "doctor", "migrations", "receipts"])
    p_trace.add_argument("--trace-id")
    p_trace.add_argument("--transaction-id")
    p_trace.add_argument("--event-id")
    p_trace.add_argument("--limit", type=int, default=10)


def handle_cli(args) -> None:
    rt = LifeEngineRuntime()
    try:
        action = getattr(args, "lifeengine_action", None)
        if not action or action == "status":
            print(format_result(rt.status()))
        elif action == "doctor":
            print(format_result(rt.doctor(level=args.level, include_samples=args.include_samples)))
        elif action == "upgrade":
            _handle_upgrade_cli(rt, args)
        elif action == "setup":
            print(format_result(rt.setup(" ".join(args.text) if args.text else None)))
        elif action == "commit":
            print(format_result(rt.commit_canon(activate=not args.no_activate)))
        elif action == "branch":
            print(format_result(rt.branch(args.name)))
        elif action == "control":
            print(format_result(rt.control(args.action, reason=args.reason)))
        elif action == "module":
            print(format_result(rt.control("module", key=args.key, value=args.value)))
        elif action == "heartbeat":
            if args.mode == "install":
                print(format_result(install_heartbeat_cron(args.schedule, args.deliver, args.name, args.run)))
            elif args.mode == "status":
                print(format_result(heartbeat_installation_status(args.schedule, args.deliver, args.name)))
            elif args.mode == "run-script":
                print(format_result(run_tick_script_once()))
            elif args.mode == "test":
                script = write_tick_script()
                print(format_result(rt.upgrade("cron_test", script_path=str(script))))
            else:
                print(format_result(rt.control("heartbeat", mode=args.mode)))
        elif action == "tick":
            print(format_result(rt.tick(now=args.now)))
        elif action == "resource":
            _handle_resource_cli(rt, args)
        elif action == "inventory":
            _handle_inventory_cli(rt, args)
        elif action == "goal":
            _handle_goal_cli(rt, args)
        elif action == "autonomy":
            _handle_autonomy_cli(rt, args)
        elif action == "proactive":
            _handle_proactive_cli(rt, args)
        elif action == "execution":
            _handle_execution_cli(rt, args)
        elif action == "confirmation":
            _handle_confirmation_cli(rt, args)
        elif action == "truth":
            _handle_truth_cli(rt, args)
        elif action == "final_gate":
            print(format_result(rt.final_gate(args.action, session_id=args.session_id, turn_id=args.turn_id, response_text=args.response_text, mode=args.mode, write_report=not args.no_report, report_id=args.report_id, limit=args.limit)))
        elif action == "trace":
            print(format_result(rt.traces(args.action, trace_id=args.trace_id, transaction_id=args.transaction_id, event_id=args.event_id, limit=args.limit)))
    finally:
        rt.close()


def _handle_upgrade_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "include_details": args.include_details,
        "write_audit": not args.no_audit,
        "reason": args.reason,
        "destination": args.destination,
        "script_path": args.script_path,
        "archive_path": getattr(args, "archive_path", None),
        "root": getattr(args, "root", None),
        "memories": getattr(args, "memories", None),
        "timeout": args.timeout,
        "limit": args.limit,
        "workers": getattr(args, "workers", None),
        "items": getattr(args, "items", None),
        "report_id": getattr(args, "report_id", None),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
        "report_path": getattr(args, "report_path", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.upgrade(args.action, **payload)))


def _handle_resource_cli(rt: LifeEngineRuntime, args: Any) -> None:
    if args.action == "list":
        print(format_result(rt.resources("list")))
    elif args.action == "define":
        print(format_result(rt.resources("define", key=args.key, display_name=args.display_name or args.key, resource_class=args.resource_class, unit=args.unit, initial=args.initial)))
    elif args.action == "delta":
        print(format_result(rt.resources("delta", resource_key=args.key, delta=args.delta or 0, operation=args.operation, reason=args.reason, source="cli")))
    elif args.action == "reserve":
        print(format_result(rt.resources("reserve", resource_key=args.key, amount=args.amount or 0, reason=args.reason)))
    elif args.action == "release":
        print(format_result(rt.resources("release", reservation_id=args.reservation_id)))
    elif args.action == "reconcile":
        print(format_result(rt.resources("reconcile")))


def _handle_inventory_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "item_id": args.item_id,
        "name": args.name,
        "category": args.category,
        "quantity": args.quantity,
        "quantity_delta": args.quantity_delta,
        "unit": args.unit,
        "condition": args.condition,
        "location": args.location,
        "from_location": args.from_location,
        "to_location": args.to_location,
        "reason": args.reason,
        "meal_type": args.meal_type,
        "eaten_at": args.eaten_at,
        "food_items": args.food,
        "notes": args.notes,
        "source": args.source,
        "limit": args.limit,
        "workers": getattr(args, "workers", None),
        "items": getattr(args, "items", None),
        "report_id": getattr(args, "report_id", None),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
        "report_path": getattr(args, "report_path", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, [], "")}
    print(format_result(rt.inventory(args.action, **payload)))



def _handle_goal_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "goal_id": args.goal_id,
        "arc_id": args.arc_id,
        "event_id": args.event_id,
        "parent_event_id": args.parent_event_id,
        "child_event_id": args.child_event_id,
        "depends_on_event_id": args.depends_on_event_id,
        "title": args.title,
        "description": args.description,
        "goal_type": args.goal_type,
        "arc_type": args.arc_type,
        "current_stage": args.stage,
        "status": args.status,
        "priority": args.priority,
        "progress": args.progress,
        "progress_delta": args.progress_delta,
        "target_date": args.target_date,
        "metrics": json.loads(args.metrics or "{}"),
        "summary": json.loads(args.summary or "{}"),
        "role": args.role,
        "weight": args.weight,
        "dependency_type": args.dependency_type,
        "child_events": json.loads(args.children or "[]"),
        "reason": args.reason,
        "limit": args.limit,
        "workers": getattr(args, "workers", None),
        "items": getattr(args, "items", None),
        "report_id": getattr(args, "report_id", None),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
        "report_path": getattr(args, "report_path", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, [], "")}
    print(format_result(rt.goals(args.action, **payload)))


def _handle_autonomy_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {"decision_id": args.decision_id, "now": args.now, "manual": args.manual, "limit": args.limit}
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.autonomy(args.action, **payload)))


def _handle_proactive_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "intent_id": args.intent_id,
        "outbox_id": args.outbox_id,
        "target_user_id": args.target_user_id,
        "target_type": args.target_type,
        "target_id": args.target_id,
        "intent_type": args.intent_type,
        "summary": args.summary,
        "draft_text": args.draft_text,
        "importance": args.importance,
        "urgency": args.urgency,
        "novelty": args.novelty,
        "relationship_relevance": args.relationship_relevance,
        "privacy_level": args.privacy_level,
        "manual": args.manual,
        "reason": args.reason,
        "status": args.status,
        "limit": args.limit,
        "workers": getattr(args, "workers", None),
        "items": getattr(args, "items", None),
        "report_id": getattr(args, "report_id", None),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
        "report_path": getattr(args, "report_path", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.proactive(args.action, **payload)))


def _handle_execution_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "decision_id": args.decision_id,
        "schedule_block_id": args.schedule_block_id,
        "now": args.now,
        "limit": args.limit,
        "workers": getattr(args, "workers", None),
        "items": getattr(args, "items", None),
        "report_id": getattr(args, "report_id", None),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
        "report_path": getattr(args, "report_path", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.execution(args.action, **payload)))


def _handle_confirmation_cli(rt: LifeEngineRuntime, args: Any) -> None:
    if args.action == "list":
        print(format_result(rt.confirmation("list", "user", "anonymous-user", status=args.status, limit=args.limit)))
    elif args.action == "get":
        print(format_result(rt.confirmation("get", "user", "anonymous-user", confirmation_id=args.confirmation_id)))
    elif args.action == "propose":
        print(format_result(rt.confirmation("propose", "user", "anonymous-user", ops=json.loads(args.ops or "[]"), reason=args.reason)))
    elif args.action == "confirm":
        print(format_result(rt.confirmation("confirm", "user", "anonymous-user", confirmation_id=args.confirmation_id, note=args.note)))
    elif args.action == "reject":
        print(format_result(rt.confirmation("reject", "user", "anonymous-user", confirmation_id=args.confirmation_id, note=args.note)))


def _handle_truth_cli(rt: LifeEngineRuntime, args: Any) -> None:
    params = json.loads(args.parameters or "{}")
    result = json.loads(args.result or "{}")
    if args.action == "list":
        print(format_result(rt.truth("list", limit=args.limit)))
    elif args.action == "resolve":
        print(format_result(rt.truth("resolve", domain=args.domain, parameters=params, allow_stale=args.allow_stale)))
    elif args.action == "observe":
        print(format_result(rt.truth("observe", domain=args.domain, authority=args.authority, parameters=params, result=result, source=args.source, ttl_minutes=args.ttl)))
    elif args.action == "bind":
        print(format_result(rt.truth("bind", domain=args.domain, authority=args.authority, value=args.value, parameters=params, freshness_ttl_minutes=args.ttl, fallback=args.fallback)))



def _simple_help() -> str:
    return (
        "LifeEngine 常用命令：\n"
        "  /life                查看状态摘要\n"
        "  /life setup <设定>   进入/继续设定，不推进生活\n"
        "  /life commit         提交设定草案\n"
        "  /life pause          暂停生活推进\n"
        "  /life resume         恢复运行\n"
        "  /life run            手动推进一次 heartbeat\n"
        "  /life review         查看待确认、主动消息、FinalGate 提醒\n"
        "  /life doctor         健康检查\n"
        "  /life backup         导出备份\n"
        "  /life advanced       显示高级命令。\n\n"
        "通常不需要人类记住内部工具；Agent 会通过 life_* tools 自己提交 LifeOps、资源、日程、trace。"
    )


def _advanced_help() -> str:
    return (
        "Advanced /life commands:\n"
        "  heartbeat <mode|install|status|run-script|test> | tick | module <key> <value>\n"
        "  resource list/add <key> | inventory list/add/meals | goal list/create/decompose/progress\n"
        "  autonomy list/plan/run | proactive list/create/evaluate/outbox/send/suppress\n"
        "  execution list/run/serendipity | confirmation list/confirm/reject <id>\n"
        "  truth list/resolve/observe/bind | final_gate check/reports/get\n"
        "  upgrade [check|backup|export|import|restore|package_check|rebuild|verify|large_smoke|cron_test]\n"
        "  upgrade [integration_check|surface|api_freeze|release_readiness|acceptance|v1_rc_check]\n"
        "  branch <name> | trace [audit|verify|doctor|migrations|receipts|explain <id>]"
    )

def slash_life(raw_args: str) -> str:
    argv = shlex.split(raw_args or "")
    rt = LifeEngineRuntime()
    try:
        if not argv or argv[0] in {"status", "状态"}:
            return format_result(rt.status())
        cmd = argv[0].lower()
        rest = argv[1:]
        if cmd in {"help", "帮助", "?"}:
            return _simple_help()
        if cmd in {"advanced", "高级", "debug"}:
            return _advanced_help()
        if cmd in {"run", "推进"}:
            return format_result(rt.tick())
        if cmd in {"backup", "备份"}:
            return format_result(rt.upgrade("export"))
        if cmd in {"review", "inbox", "待办", "查看"}:
            return format_result({
                "ok": True,
                "status": rt.status(),
                "final_gate_reports": rt.final_gate("reports", limit=5).get("reports", []),
                "proactive": rt.proactive("list", limit=5),
            })
        if cmd in {"doctor", "check", "health", "诊断"}:
            return format_result(rt.doctor(include_samples=("samples" in rest or "--samples" in rest)))
        if cmd in {"upgrade", "维护", "升级"}:
            action = rest[0] if rest else "check"
            if action in {"backup", "备份"}:
                return format_result(rt.upgrade("backup", reason="/life upgrade backup"))
            if action in {"backups", "list_backups"}:
                return format_result(rt.upgrade("backups"))
            if action in {"rebuild", "rebuild_memory", "rebuild_indexes"}:
                return format_result(rt.upgrade("rebuild_memory"))
            if action in {"verify", "verify_memory", "verify_indexes"}:
                return format_result(rt.upgrade("verify_memory"))
            if action in {"export", "导出"}:
                return format_result(rt.upgrade("export"))
            if action in {"exports", "list_exports"}:
                return format_result(rt.upgrade("exports"))
            if action in {"inspect_export", "inspect"} and len(rest) > 1:
                return format_result(rt.upgrade("inspect_export", archive_path=rest[1]))
            if action in {"import", "导入"} and len(rest) > 1:
                return format_result(rt.upgrade("import", archive_path=rest[1]))
            if action in {"restore", "恢复"} and len(rest) > 1:
                return format_result(rt.upgrade("restore", archive_path=rest[1]))
            if action in {"package", "package_check", "checksum"}:
                return format_result(rt.upgrade("package_check"))
            if action in {"large_smoke", "smoke"}:
                return format_result(rt.upgrade("large_smoke"))
            if action in {"maintenance", "runs"}:
                return format_result(rt.upgrade("maintenance"))
            if action in {"cron_test", "heartbeat_test", "test"}:
                script = write_tick_script()
                return format_result(rt.upgrade("cron_test", script_path=str(script)))
            if action in {"surface", "tools"}:
                return format_result(rt.upgrade("surface"))
            if action in {"integration", "integration_check"}:
                return format_result(rt.upgrade("integration_check", include_details=("details" in rest or "--details" in rest)))
            if action in {"api_freeze", "freeze"}:
                return format_result(rt.upgrade("api_freeze"))
            if action in {"api_freeze_status", "freeze_status"}:
                return format_result(rt.upgrade("api_freeze_status"))
            if action in {"release", "release_readiness", "v1_rc_check"}:
                return format_result(rt.upgrade("release_readiness", include_details=("details" in rest or "--details" in rest)))
            if action in {"mandatory_gate_patch", "patch"}:
                return format_result(rt.upgrade("mandatory_gate_patch"))
            if action in {"concurrency_smoke", "schedule_overlap_smoke", "heartbeat_idempotency_smoke", "lifeops_stress"}:
                return format_result(rt.upgrade(action))
            if action in {"acceptance", "acceptance_suite", "v1_rc_acceptance"}:
                return format_result(rt.upgrade("acceptance"))
            if action in {"acceptance_reports"}:
                return format_result(rt.upgrade("acceptance_reports"))
            if action in {"acceptance_report"} and len(rest) > 1:
                return format_result(rt.upgrade("acceptance_report", report_id=rest[1]))
            if action in {"acceptance_runs"}:
                return format_result(rt.upgrade("acceptance_runs", acceptance_run_id=(rest[1] if len(rest) > 1 else None)))
            if action in {"v1_rc_checklists", "v1_rc_checklist"}:
                return format_result(rt.upgrade("v1_rc_checklists"))
            return format_result(rt.upgrade("check", include_details=("details" in rest or "--details" in rest)))
        if cmd in {"setup", "设定"}:
            return format_result(rt.setup(" ".join(rest) if rest else None))
        if cmd in {"commit", "提交"}:
            return format_result(rt.commit_canon())
        if cmd in {"pause", "暂停"}:
            return format_result(rt.control("pause", reason="/life pause"))
        if cmd in {"resume", "恢复", "start", "开始"}:
            return format_result(rt.control("resume", reason="/life resume"))
        if cmd in {"disable", "关闭"}:
            return format_result(rt.control("disable", reason="/life disable"))
        if cmd == "heartbeat":
            mode = rest[0] if rest else "manual"
            if mode == "install":
                return format_result(install_heartbeat_cron())
            if mode == "status":
                return format_result(heartbeat_installation_status())
            if mode in {"run-script", "run_script"}:
                return format_result(run_tick_script_once())
            if mode == "test":
                script = write_tick_script()
                return format_result(rt.upgrade("cron_test", script_path=str(script)))
            return format_result(rt.control("heartbeat", mode=mode))
        if cmd == "tick":
            return format_result(rt.tick())
        if cmd == "module" and len(rest) >= 2:
            return format_result(rt.control("module", key=rest[0], value=rest[1]))
        if cmd == "resource":
            if not rest or rest[0] == "list":
                return format_result(rt.resources("list"))
            if rest[0] in {"add", "define"} and len(rest) >= 2:
                key = rest[1]
                return format_result(rt.resources("define", key=key, display_name=key, resource_class="capacity", unit="points", initial=50))
        if cmd in {"inventory", "inv", "items"}:
            if not rest or rest[0] == "list":
                return format_result(rt.inventory("list"))
            if rest[0] in {"add", "create"} and len(rest) >= 2:
                return format_result(rt.inventory("add", name=" ".join(rest[1:]), category="other", quantity=1, source="slash"))
            if rest[0] == "meals":
                return format_result(rt.inventory("meals"))
        if cmd in {"goal", "goals", "目标"}:
            if not rest or rest[0] == "list":
                return format_result(rt.goals("list"))
            if rest[0] in {"create", "add"} and len(rest) >= 2:
                return format_result(rt.goals("create", title=" ".join(rest[1:])))
            if rest[0] == "arcs":
                return format_result(rt.goals("arcs"))
            if rest[0] == "decompose" and len(rest) >= 3:
                try:
                    children = json.loads(" ".join(rest[2:]))
                except Exception:
                    children = [{"title": " ".join(rest[2:])}]
                return format_result(rt.goals("decompose", parent_event_id=rest[1], child_events=children))
            if rest[0] == "progress" and len(rest) >= 2:
                payload = {"goal_id": rest[1]}
                if len(rest) >= 3:
                    try:
                        payload["progress_delta"] = float(rest[2])
                    except Exception:
                        payload["reason"] = " ".join(rest[2:])
                return format_result(rt.goals("progress", **payload))
        if cmd in {"autonomy", "auto", "自主"}:
            if not rest or rest[0] == "list":
                return format_result(rt.autonomy("list"))
            if rest[0] in {"plan", "run"}:
                return format_result(rt.autonomy(rest[0], manual=True))
            if rest[0] == "get" and len(rest) >= 2:
                return format_result(rt.autonomy("get", decision_id=rest[1]))
        if cmd in {"proactive", "pro", "主动"}:
            if not rest or rest[0] in {"list", "intents"}:
                return format_result(rt.proactive("list"))
            if rest[0] == "outbox":
                return format_result(rt.proactive("outbox"))
            if rest[0] == "state":
                return format_result(rt.proactive("state"))
            if rest[0] in {"create", "intent"} and len(rest) >= 2:
                return format_result(rt.proactive("create", summary=" ".join(rest[1:]), target_type="self_journal", intent_type="share_interesting"))
            if rest[0] in {"evaluate", "queue"}:
                payload = {"manual": False}
                if len(rest) >= 2:
                    payload["intent_id"] = rest[1]
                return format_result(rt.proactive("evaluate", **payload))
            if rest[0] in {"send", "sent"} and len(rest) >= 2:
                return format_result(rt.proactive("send", outbox_id=rest[1], manual=True))
            if rest[0] == "suppress" and len(rest) >= 2:
                return format_result(rt.proactive("suppress", intent_id=rest[1], reason=" ".join(rest[2:]) or "slash suppress"))

        if cmd in {"execution", "exec", "执行"}:
            if not rest or rest[0] in {"list", "decisions"}:
                return format_result(rt.execution("list"))
            if rest[0] == "serendipity":
                return format_result(rt.execution("serendipity"))
            if rest[0] == "get" and len(rest) >= 2:
                return format_result(rt.execution("get", decision_id=rest[1]))
            if rest[0] in {"run", "simulate", "execute"}:
                payload = {}
                if len(rest) >= 2:
                    payload["schedule_block_id"] = rest[1]
                return format_result(rt.execution(rest[0], **payload))
        if cmd in {"confirmation", "confirm"}:
            if not rest or rest[0] == "list":
                return format_result(rt.confirmation("list", "user", "anonymous-user"))
            if rest[0] in {"confirm", "approve"} and len(rest) >= 2:
                return format_result(rt.confirmation("confirm", "user", "anonymous-user", confirmation_id=rest[1], note=" ".join(rest[2:])))
            if rest[0] == "reject" and len(rest) >= 2:
                return format_result(rt.confirmation("reject", "user", "anonymous-user", confirmation_id=rest[1], note=" ".join(rest[2:])))
        if cmd == "truth":
            if not rest or rest[0] == "list":
                return format_result(rt.truth("list"))
            if rest[0] == "resolve" and len(rest) >= 2:
                return format_result(rt.truth("resolve", domain=rest[1]))
            if rest[0] == "observe" and len(rest) >= 3:
                try:
                    observed = json.loads(" ".join(rest[2:]))
                except Exception:
                    observed = {"text": " ".join(rest[2:])}
                return format_result(rt.truth("observe", domain=rest[1], result=observed, source="slash"))
            if rest[0] == "bind" and len(rest) >= 3:
                return format_result(rt.truth("bind", domain=rest[1], authority=rest[2]))
        if cmd == "branch" and rest:
            return format_result(rt.branch(" ".join(rest)))
        if cmd in {"final_gate", "finalgate", "gate", "最终审计"}:
            if not rest or rest[0] in {"reports", "list"}:
                return format_result(rt.final_gate("reports"))
            if rest[0] in {"get", "explain"} and len(rest) >= 2:
                return format_result(rt.final_gate("get", report_id=rest[1]))
            if rest[0] in {"check", "audit", "simulate"}:
                return format_result(rt.final_gate("check", response_text=" ".join(rest[1:]), mode="repair"))
        if cmd == "trace":
            if not rest:
                return format_result(rt.traces("latest"))
            if rest[0] in {"audit", "verify", "migrations", "receipts"}:
                return format_result(rt.traces(rest[0]))
            if rest[0] == "explain" and len(rest) >= 2:
                token = rest[1]
                kw: dict[str, Any] = {"trace_id": token} if token.startswith("trace_") else {"transaction_id": token} if token.startswith("tx_") else {"event_id": token}
                return format_result(rt.traces("explain", **kw))
        return _simple_help()
    finally:
        rt.close()
