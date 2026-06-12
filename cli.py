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

    p_interface = sub.add_parser("interface", help="Unified safe LifeEngine interface catalog/read/write")
    p_interface.add_argument("interface_action", nargs="?", default="catalog", choices=["catalog", "read", "write"])
    p_interface.add_argument("domain", nargs="?")
    p_interface.add_argument("view_or_intent", nargs="?")
    p_interface.add_argument("--payload", default="{}", help="JSON payload for interface action")

    p_living = sub.add_parser("living", help="Concrete living layer: rhythm, Canon consistency, inventory presets, paper notes")
    p_living.add_argument("action", nargs="?", default="summary", choices=["summary", "consistency", "init_inventory", "day_rhythm", "decompose_abstract", "paper_notes", "create_note", "diary_draft"])
    p_living.add_argument("text", nargs="*", help="Optional note text or additional args")
    p_living.add_argument("--preset", default="default")
    p_living.add_argument("--date")
    p_living.add_argument("--timezone", default="Asia/Tokyo")
    p_living.add_argument("--event-id")
    p_living.add_argument("--limit", type=int, default=20)



    p_closet = sub.add_parser("closet", help="Human-friendly wardrobe/shoes/socks/accessories/vanity collections")
    p_closet.add_argument("action", nargs="?", default="summary", choices=["summary", "init", "collections", "wardrobe", "shoes", "socks", "accessories", "vanity", "items", "add", "add_item", "create_collection", "update_collection", "archive_collection", "generate_assets", "checkout", "return", "maintain", "outfit", "outfits", "presets"])
    p_closet.add_argument("text", nargs="*", help="Optional item name/description or collection name")
    p_closet.add_argument("--collection-id")
    p_closet.add_argument("--collection-type")
    p_closet.add_argument("--item-id")
    p_closet.add_argument("--asset-id")
    p_closet.add_argument("--asset-uri")
    p_closet.add_argument("--type")
    p_closet.add_argument("--name")
    p_closet.add_argument("--description")
    p_closet.add_argument("--quantity", type=float, default=1)
    p_closet.add_argument("--occasion", default="daily")
    p_closet.add_argument("--limit", type=int, default=50)

    p_behavior = sub.add_parser("behavior", help="Private behavior mappings from public life actions to hidden information sources")
    p_behavior.add_argument("action", nargs="?", default="summary", choices=["summary", "init", "presets", "list", "get", "create", "update", "archive", "resolve", "sources", "add_source", "update_source", "runs", "observe", "redact", "explain"])
    p_behavior.add_argument("text", nargs="*", help="Optional behavior description / source notes")
    p_behavior.add_argument("--behavior-key")
    p_behavior.add_argument("--mapping-id")
    p_behavior.add_argument("--narrative-label")
    p_behavior.add_argument("--public-label")
    p_behavior.add_argument("--name")
    p_behavior.add_argument("--description")
    p_behavior.add_argument("--source-id")
    p_behavior.add_argument("--source-type")
    p_behavior.add_argument("--url")
    p_behavior.add_argument("--query-template")
    p_behavior.add_argument("--include-private", action="store_true")
    p_behavior.add_argument("--limit", type=int, default=50)

    p_webui = sub.add_parser("webui", help="Run LifeEngine WebUI / Observatory")
    p_webui.add_argument("--life-dir", default=None, help="LifeEngine directory or lifeengine.db path")
    p_webui.add_argument("--host", default="127.0.0.1")
    p_webui.add_argument("--port", type=int, default=8765)
    p_webui.add_argument("--open", action="store_true")

    p_schedule = sub.add_parser("schedule", help="Show human-readable schedule timeline")
    p_schedule.add_argument("period", nargs="?", default="today", help="today, tomorrow, week, or YYYY-MM-DD")
    p_schedule.add_argument("date", nargs="?", help="Optional YYYY-MM-DD date")
    p_schedule.add_argument("--timezone", default=None)
    p_schedule.add_argument("--limit", type=int, default=200)
    p_schedule.add_argument("--no-completed", action="store_true")

    p_config = sub.add_parser("config", help="Show/read/write LifeEngine required settings and CanonDraft")
    p_config.add_argument("action", nargs="?", default="summary", choices=["summary", "check", "latest", "requirements", "suggest_defaults", "apply_default_draft", "draft", "explain", "patch", "set"])
    p_config.add_argument("path", nargs="?")
    p_config.add_argument("value", nargs="*")
    p_config.add_argument("--kind", default="balanced")
    p_config.add_argument("--text")

    p_review = sub.add_parser("review", help="Show one-page human LifeEngine review")
    p_review.add_argument("action", nargs="?", default="summary", choices=["summary", "run", "review", "runs", "history", "get", "dismiss", "preview", "apply", "actions", "get_action", "policy", "set_policy", "batch_preview", "apply_all", "batch_runs", "get_batch", "undo_preview", "undo", "undo_runs", "get_undo", "batch_undo_preview", "batch_undo", "managed_preview", "managed_run", "managed_runs", "get_managed_run", "managed_state", "managed_acceptance", "managed_acceptance_runs", "get_managed_acceptance", "managed_stress", "managed_stress_runs", "get_managed_stress", "managed_observability", "managed_observability_reports", "get_managed_observability", "managed_readiness", "managed_readiness_reports", "get_managed_readiness"] )
    p_review.add_argument("target", nargs="?", help="review_run_id for get, item_id for dismiss/apply/preview, action_run_id for get_action")
    p_review.add_argument("--choice", help="explicit choice for ambiguous actions: confirm/reject or send/suppress")
    p_review.add_argument("--dry-run", action="store_true")
    p_review.add_argument("--allow-policy-patch", action="store_true")
    p_review.add_argument("--section")
    p_review.add_argument("--safe-only", action="store_true", default=True)
    p_review.add_argument("--policy-patch", help="JSON review action policy patch")
    p_review.add_argument("--limit", type=int, default=5)
    p_review.add_argument("--count", type=int, default=25)
    p_review.add_argument("--stress-count", type=int, default=12)
    p_review.add_argument("--no-doctor", action="store_true")
    p_review.add_argument("--json", action="store_true", dest="as_json")

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
            "surface", "integration_check", "api_freeze", "api_freeze_status",
            "release_readiness", "v1_rc_check", "mandatory_gate_patch",
            "concurrency_smoke", "schedule_overlap_smoke",
            "heartbeat_idempotency_smoke", "lifeops_stress",
            "acceptance", "acceptance_suite", "v1_rc_acceptance",
            "acceptance_reports", "acceptance_report", "acceptance_runs",
            "v1_rc_checklists", "sleep_reply_dream_acceptance",
            "srd_acceptance", "sleep_dream_acceptance",
            "sleep_reply_dream_acceptance_runs", "srd_acceptance_runs",
            "sleep_reply_dream_acceptance_get", "srd_acceptance_get",
            "sleep_autonomy_execution_acceptance", "sae_acceptance",
            "sleep_autonomy_execution_acceptance_runs", "sae_acceptance_runs",
            "sleep_autonomy_execution_acceptance_get", "sae_acceptance_get",
            "sleep_reply_dream_conversation_acceptance", "crd_acceptance",
            "conversation_acceptance", "srd_conversation_acceptance",
            "sleep_reply_dream_conversation_acceptance_runs", "crd_acceptance_runs",
            "sleep_reply_dream_conversation_acceptance_get", "crd_acceptance_get",
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
    p_autonomy.add_argument("action", choices=["list", "get", "plan", "run", "sleep_context", "sleep_adjustments"])
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
    p_execution.add_argument("action", choices=["list", "decisions", "get", "run", "simulate", "execute", "serendipity", "sleep_context", "sleep_adjustments"])
    p_execution.add_argument("--decision-id")
    p_execution.add_argument("--schedule-block-id")
    p_execution.add_argument("--now", default=None)
    p_execution.add_argument("--limit", type=int, default=20)


    p_policy = sub.add_parser("policy", help="Sleep/Reply/Dream policy UX configuration")
    p_policy.add_argument("action", choices=["get", "status", "summary", "explain", "set", "patch", "preset", "profile", "reset", "suggestions", "suggest", "audits", "history", "conflicts", "check_conflicts", "conflict_reports", "export", "exports", "inspect_import", "inspect_export", "import", "imports", "acceptance", "acceptance_runs", "acceptance_get"])
    p_policy.add_argument("--preset", "--profile", dest="preset")
    p_policy.add_argument("--patch", "--policy-patch", dest="policy_patch", default=None, help="JSON object deep-merge patch")
    p_policy.add_argument("--status")
    p_policy.add_argument("--limit", type=int, default=20)
    p_policy.add_argument("--no-record", action="store_true")
    p_policy.add_argument("--destination")
    p_policy.add_argument("--path")
    p_policy.add_argument("--apply", action="store_true")
    p_policy.add_argument("--acceptance-run-id")

    p_sleep = sub.add_parser("sleep", help="Sleep plans and sleep sessions")
    p_sleep.add_argument("action", choices=["status", "plan", "plan_day", "nap", "start", "wake", "skip", "plans", "sessions", "get_plan", "get_session"])
    p_sleep.add_argument("--sleep-plan-id")
    p_sleep.add_argument("--sleep-session-id")
    p_sleep.add_argument("--schedule-block-id")
    p_sleep.add_argument("--date")
    p_sleep.add_argument("--planned-start")
    p_sleep.add_argument("--planned-end")
    p_sleep.add_argument("--bedtime", default="23:30")
    p_sleep.add_argument("--wake-time", default="07:00")
    p_sleep.add_argument("--timezone-name", default="UTC")
    p_sleep.add_argument("--sleep-type", default="core_sleep")
    p_sleep.add_argument("--wake-policy", default="natural")
    p_sleep.add_argument("--alarm-at")
    p_sleep.add_argument("--now")
    p_sleep.add_argument("--wake-cause", default="natural")
    p_sleep.add_argument("--interrupted-by-user", action="store_true")
    p_sleep.add_argument("--quality-score", type=int)
    p_sleep.add_argument("--reason", default="manual CLI")
    p_sleep.add_argument("--status")
    p_sleep.add_argument("--limit", type=int, default=20)

    p_dream = sub.add_parser("dream", help="DreamRun, DreamAudit, dream entries, and wake-share intents")
    p_dream.add_argument("action", choices=["status", "run", "audit", "repair_plan", "repair", "repairs", "list", "runs", "entries", "findings", "get", "get_entry", "create_entry"])
    p_dream.add_argument("--dream-run-id")
    p_dream.add_argument("--dream-entry-id")
    p_dream.add_argument("--sleep-session-id")
    p_dream.add_argument("--force", action="store_true")
    p_dream.add_argument("--allow-nap", action="store_true")
    p_dream.add_argument("--no-share", action="store_true")
    p_dream.add_argument("--target-user-id")
    p_dream.add_argument("--content")
    p_dream.add_argument("--summary")
    p_dream.add_argument("--share-text")
    p_dream.add_argument("--status")
    p_dream.add_argument("--severity")
    p_dream.add_argument("--limit", type=int, default=20)
    p_dream.add_argument("--dry-run", action="store_true")

    p_reply = sub.add_parser("reply", help="ReplyGate, delayed replies, and call override")
    p_reply.add_argument("action", choices=["status", "assess", "gate", "defer", "queue", "release", "list", "delayed", "calls", "doctor", "call"] )
    p_reply.add_argument("--message-text", "--text", dest="message_text")
    p_reply.add_argument("--user-id")
    p_reply.add_argument("--session-id")
    p_reply.add_argument("--turn-id")
    p_reply.add_argument("--gate-decision-id")
    p_reply.add_argument("--reason", default="manual CLI")
    p_reply.add_argument("--force-call", action="store_true")
    p_reply.add_argument("--status", default=None)
    p_reply.add_argument("--limit", type=int, default=20)

    p_call = sub.add_parser("call", help="Emergency wake/interrupt call override")
    p_call.add_argument("--reason", default="manual CLI call override")
    p_call.add_argument("--message-text", "--text", dest="message_text")
    p_call.add_argument("--user-id")
    p_call.add_argument("--session-id")
    p_call.add_argument("--turn-id")

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
        if action == "webui":
            rt.close()
            from .webui.server import main as _webui_main
            argv = ["--host", args.host, "--port", str(args.port)]
            if args.life_dir:
                argv.extend(["--life-dir", args.life_dir])
            if args.open:
                argv.append("--open")
            raise SystemExit(_webui_main(argv))
        if action == "interface":
            payload = json.loads(getattr(args, "payload", "{}") or "{}")
            iaction = getattr(args, "interface_action", "catalog") or "catalog"
            if getattr(args, "domain", None):
                payload["domain"] = args.domain
            if iaction == "read" and getattr(args, "view_or_intent", None):
                payload["view"] = args.view_or_intent
            if iaction == "write" and getattr(args, "view_or_intent", None):
                payload["intent"] = args.view_or_intent
            print(format_result(rt.interface(iaction, **payload)))
        elif not action or action == "status":
            print(format_result(rt.status()))
        elif action == "schedule":
            period = args.period or "today"
            date = args.date
            if period and period[0].isdigit():
                date = period
                period = "day"
            print(format_result(rt.schedule(period, date=date, timezone=args.timezone, include_completed=not args.no_completed, limit=args.limit)))
        elif action == "config":
            caction = getattr(args, "action", "summary")
            payload = {"kind": getattr(args, "kind", "balanced")}
            if caction in {"patch", "set"}:
                if getattr(args, "text", None):
                    payload["text"] = args.text
                elif getattr(args, "path", None):
                    payload["path"] = args.path
                    payload["value"] = " ".join(getattr(args, "value", []) or [])
            print(format_result(rt.required_settings(caction, **payload)))
        elif action == "living":
            payload = {"preset": args.preset, "date": args.date, "timezone": args.timezone, "event_id": args.event_id, "limit": args.limit}
            if args.text:
                payload["summary"] = " ".join(args.text)
                payload["text"] = " ".join(args.text)
            print(format_result(rt.living(args.action, **{k:v for k,v in payload.items() if v is not None})))
        elif action == "closet":
            payload = {"collection_id": args.collection_id, "collection_type": args.collection_type or args.type, "item_id": args.item_id, "asset_id": args.asset_id, "asset_uri": args.asset_uri, "quantity": args.quantity, "occasion": args.occasion, "limit": args.limit}
            if args.name:
                payload["name"] = args.name
            elif args.text:
                payload["name"] = args.text[0]
                if len(args.text) > 1:
                    payload["description"] = " ".join(args.text[1:])
            if args.description:
                payload["description"] = args.description
            print(format_result(rt.collection(args.action, **{k:v for k,v in payload.items() if v not in (None, "")})))

        elif action == "behavior":
            payload = {"behavior_key": args.behavior_key, "mapping_id": args.mapping_id, "narrative_label": args.narrative_label or args.public_label or args.name, "name": args.name, "description": args.description, "source_id": args.source_id, "source_type": args.source_type, "url": args.url, "query_template": args.query_template, "include_private": args.include_private, "limit": args.limit}
            if args.text:
                txt = " ".join(args.text)
                if args.action in {"create", "update"}:
                    payload.setdefault("narrative_label", txt)
                elif args.action in {"add_source", "update_source"}:
                    payload.setdefault("name", txt)
                elif args.action == "redact":
                    payload["text"] = txt
                else:
                    payload["behavior_text"] = txt
                    payload["summary"] = txt
            print(format_result(rt.behavior(args.action, **{k:v for k,v in payload.items() if v not in (None, "", [])})))

        elif action == "doctor":
            print(format_result(rt.doctor(level=args.level, include_samples=args.include_samples)))
        elif action == "review":
            if args.action in {"runs", "history"}:
                print(format_result(rt.review("runs", limit=args.limit)))
            elif args.action == "get":
                print(format_result(rt.review("get_run", review_run_id=args.target)))
            elif args.action == "dismiss":
                print(format_result(rt.review("dismiss", item_id=args.target)))
            elif args.action == "preview":
                print(format_result(rt.review("preview_action", item_id=args.target, choice=args.choice, dry_run=True)))
            elif args.action == "apply":
                print(format_result(rt.review("apply", item_id=args.target, choice=args.choice, dry_run=args.dry_run, allow_policy_patch=args.allow_policy_patch)))
            elif args.action == "actions":
                print(format_result(rt.review("action_runs", item_id=args.target, limit=args.limit)))
            elif args.action == "get_action":
                print(format_result(rt.review("get_action", action_run_id=args.target)))
            elif args.action == "policy":
                print(format_result(rt.review("policy")))
            elif args.action == "set_policy":
                patch = json.loads(args.policy_patch or "{}")
                print(format_result(rt.review("set_policy", policy_patch=patch)))
            elif args.action == "batch_preview":
                print(format_result(rt.review("batch_preview", review_run_id=args.target, section=args.section, safe_only=args.safe_only, limit=args.limit, dry_run=True)))
            elif args.action == "apply_all":
                print(format_result(rt.review("apply_all", review_run_id=args.target, section=args.section, safe_only=args.safe_only, limit=args.limit, dry_run=args.dry_run)))
            elif args.action == "batch_runs":
                print(format_result(rt.review("batch_runs", limit=args.limit)))
            elif args.action == "get_batch":
                print(format_result(rt.review("get_batch", batch_run_id=args.target)))
            elif args.action == "undo_preview":
                print(format_result(rt.review("undo_preview", action_run_id=args.target)))
            elif args.action == "undo":
                print(format_result(rt.review("undo", action_run_id=args.target, dry_run=args.dry_run, reason="CLI review undo")))
            elif args.action == "undo_runs":
                print(format_result(rt.review("undo_runs", limit=args.limit)))
            elif args.action == "get_undo":
                print(format_result(rt.review("get_undo", undo_run_id=args.target)))
            elif args.action == "batch_undo_preview":
                print(format_result(rt.review("batch_undo_preview", batch_run_id=args.target)))
            elif args.action == "batch_undo":
                print(format_result(rt.review("batch_undo", batch_run_id=args.target, dry_run=args.dry_run, safe_only=args.safe_only, reason="CLI review batch undo")))
            elif args.action == "managed_preview":
                print(format_result(rt.review("managed_preview", trigger_source="cli", dry_run=True, force=args.dry_run)))
            elif args.action == "managed_run":
                print(format_result(rt.review("managed_run", trigger_source="cli", dry_run=args.dry_run, force=True)))
            elif args.action == "managed_runs":
                print(format_result(rt.review("managed_runs", limit=args.limit)))
            elif args.action == "get_managed_run":
                print(format_result(rt.review("get_managed_run", managed_run_id=args.target)))
            elif args.action == "managed_state":
                print(format_result(rt.review("managed_state")))
            elif args.action == "managed_acceptance":
                print(format_result(rt.review("managed_acceptance", stress_count=args.stress_count)))
            elif args.action == "managed_acceptance_runs":
                print(format_result(rt.review("managed_acceptance_runs", limit=args.limit)))
            elif args.action == "get_managed_acceptance":
                print(format_result(rt.review("get_managed_acceptance", acceptance_run_id=args.target)))
            elif args.action == "managed_stress":
                print(format_result(rt.review("managed_stress", count=args.count, limit=args.limit)))
            elif args.action == "managed_stress_runs":
                print(format_result(rt.review("managed_stress_runs", limit=args.limit)))
            elif args.action == "get_managed_stress":
                print(format_result(rt.review("get_managed_stress", stress_run_id=args.target)))
            elif args.action == "managed_observability":
                print(format_result(rt.review("managed_observability")))
            elif args.action == "managed_observability_reports":
                print(format_result(rt.review("managed_observability_reports", limit=args.limit)))
            elif args.action == "get_managed_observability":
                print(format_result(rt.review("get_managed_observability", report_id=args.target)))
            elif args.action == "managed_readiness":
                print(format_result(rt.review("managed_release_readiness")))
            elif args.action == "managed_readiness_reports":
                print(format_result(rt.review("managed_release_readiness_reports", limit=args.limit)))
            elif args.action == "get_managed_readiness":
                print(format_result(rt.review("get_managed_release_readiness", report_id=args.target)))
            else:
                out = rt.review("summary", include_doctor=not args.no_doctor, limit=args.limit)
                print(format_result(out) if args.as_json else out.get("rendered", format_result(out)))
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
        elif action == "policy":
            _handle_policy_cli(rt, args)
        elif action == "sleep":
            _handle_sleep_cli(rt, args)
        elif action == "dream":
            _handle_dream_cli(rt, args)
        elif action == "reply":
            _handle_reply_cli(rt, args)
        elif action == "call":
            print(format_result(rt.call(reason=args.reason, message_text=args.message_text, user_id=args.user_id, session_id=args.session_id, turn_id=args.turn_id)))
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



def _handle_policy_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "preset": args.preset,
        "policy_patch": json.loads(args.policy_patch) if args.policy_patch else None,
        "status": args.status,
        "limit": args.limit,
        "record": not args.no_record,
        "destination": getattr(args, "destination", None),
        "path": getattr(args, "path", None),
        "apply": getattr(args, "apply", False),
        "acceptance_run_id": getattr(args, "acceptance_run_id", None),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.policy(args.action, **payload)))

def _handle_sleep_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "sleep_plan_id": args.sleep_plan_id,
        "sleep_session_id": args.sleep_session_id,
        "schedule_block_id": args.schedule_block_id,
        "date": args.date,
        "planned_start": args.planned_start,
        "planned_end": args.planned_end,
        "bedtime": args.bedtime,
        "wake_time": args.wake_time,
        "timezone_name": args.timezone_name,
        "sleep_type": args.sleep_type,
        "wake_policy": args.wake_policy,
        "alarm_at": args.alarm_at,
        "now": args.now,
        "wake_cause": args.wake_cause,
        "interrupted_by_user": args.interrupted_by_user,
        "quality_score": args.quality_score,
        "reason": args.reason,
        "status": args.status,
        "limit": args.limit,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.sleep_tool(args.action, **payload)))


def _handle_dream_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "dream_run_id": args.dream_run_id,
        "dream_entry_id": args.dream_entry_id,
        "sleep_session_id": args.sleep_session_id,
        "force": args.force,
        "allow_nap": args.allow_nap,
        "create_share_intent": not args.no_share,
        "target_user_id": args.target_user_id,
        "content": args.content,
        "summary": args.summary,
        "share_text": args.share_text,
        "status": args.status,
        "severity": args.severity,
        "limit": args.limit,
        "dry_run": getattr(args, "dry_run", False),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.dream(args.action, **payload)))


def _handle_reply_cli(rt: LifeEngineRuntime, args: Any) -> None:
    payload = {
        "message_text": args.message_text,
        "user_id": args.user_id,
        "gate_decision_id": args.gate_decision_id,
        "reason": args.reason,
        "force_call": args.force_call,
        "status": args.status,
        "limit": args.limit,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    print(format_result(rt.reply(args.action, session_id=args.session_id, turn_id=args.turn_id, **payload)))


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
        "  /life schedule       查看今天日程；支持 tomorrow/week/日期\n"
        "  /life config         查看必选设定检查\n"
        "  /life setup <设定>   进入/继续设定，不推进生活\n"
        "  /life commit         提交设定草案\n"
        "  /life pause          暂停生活推进\n"
        "  /life resume         恢复运行\n"
        "  /life run            手动推进一次 heartbeat\n"
        "  /life call           紧急叫醒/打断，释放延迟回复\n"
        "  /life dream          查看/运行 Dream 自检与梦境分享意图\n"
        "  /life policy         查看睡眠/回复/梦分享策略\n"
        "  /life review         人类可读待办/建议列表\n"
        "  /life behavior       查看/设置行为映射（内部真相源不对用户暴露）\n"
        "  /life closet         查看衣橱/鞋柜/袜子/配饰/梳妆台\n"
        "  /life webui          启动 WebUI 观察台\n"
        "  /life doctor         健康检查\n"
        "  /life backup         导出备份\n"
        "  /life advanced       显示高级命令。\n\n"
        "普通人只看 schedule/review/config；复杂 life_* 工具交给 Agent 自己用。通常不需要人类记住内部工具。\n"
        "日程说明：/life schedule explain；待排期事项：/life schedule unscheduled。"
    )


def _advanced_help() -> str:
    return (
        "Advanced /life commands:\n"
        "  heartbeat <mode|install|status|run-script|test> | tick | module <key> <value>\n"
        "  resource list/add <key> | inventory list/add/meals | closet wardrobe/add/outfit | goal list/create/decompose/progress\n"
        "  autonomy list/plan/run/sleep_context | proactive list/create/evaluate/outbox/send/suppress\n"
        "  execution list/run/serendipity | sleep status/plan/start/wake/plans/sessions | dream status/run/entries/findings | reply status/list/release/doctor | call | confirmation list/confirm/reject <id>\n"
        "  truth list/resolve/observe/bind | behavior summary/init/resolve/add_source | final_gate check/reports/get\n"
        "  upgrade [check|backup|export|import|restore|package_check|rebuild|verify|large_smoke|cron_test]\n"
        "  upgrade [integration_check|surface|api_freeze|release_readiness|acceptance|v1_rc_check]\n"
        "  branch <name> | trace [audit|verify|doctor|migrations|receipts|explain <id>] | webui"
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
        if cmd in {"interface", "接口", "io"}:
            if not rest:
                return format_result(rt.interface("catalog"))
            action2 = rest[0]
            domain = rest[1] if len(rest) > 1 else None
            view = rest[2] if len(rest) > 2 else None
            if action2 in {"read", "读"}:
                return format_result(rt.interface("read", domain=domain, view=view))
            if action2 in {"write", "写"}:
                return format_result(rt.interface("write", domain=domain, intent=view, text=" ".join(rest[3:])))
            return format_result(rt.interface("catalog"))
        if cmd in {"advanced", "高级", "debug"}:
            return _advanced_help()
        if cmd in {"behavior", "行为映射", "映射"}:
            action = rest[0] if rest else "summary"
            payload = {}
            aliases = {"说明": "explain", "协议": "explain", "初始化": "init", "预设": "presets", "列表": "list", "解析": "resolve", "使用": "resolve", "创建": "create", "新增": "create", "更新": "update", "归档": "archive", "历史": "runs", "来源": "sources", "添加来源": "add_source"}
            action = aliases.get(action, action)
            if action in {"resolve", "get", "archive", "runs", "sources", "add_source"}:
                if len(rest) > 1:
                    payload["behavior_key"] = rest[1]
            if action in {"create", "update"}:
                if len(rest) > 1:
                    payload["behavior_key"] = rest[1]
                if len(rest) > 2:
                    payload["narrative_label"] = rest[2]
                if len(rest) > 3:
                    payload["description"] = " ".join(rest[3:])
            if action == "add_source" and len(rest) > 2:
                payload["name"] = " ".join(rest[2:])
            return format_result(rt.behavior(action, **payload))

        if cmd in {"closet", "衣橱", "衣柜", "鞋柜", "袜子", "配饰", "梳妆台"}:
            action = rest[0] if rest else "summary"
            aliases = {"初始化": "init", "衣橱": "wardrobe", "衣柜": "wardrobe", "鞋柜": "shoes", "袜子": "socks", "配饰": "accessories", "梳妆台": "vanity", "穿搭": "outfit", "添加": "add", "新增": "add"}
            action = aliases.get(action, action)
            payload = {}
            if action in {"add", "add_item"}:
                if len(rest) > 1:
                    payload["collection_type"] = rest[1]
                if len(rest) > 2:
                    payload["name"] = rest[2]
                    payload["description"] = " ".join(rest[3:]) if len(rest) > 3 else rest[2]
            elif action == "create_collection":
                if len(rest) > 1:
                    payload["collection_type"] = rest[1]
                if len(rest) > 2:
                    payload["name"] = rest[2]
            elif len(rest) > 1:
                payload["text"] = " ".join(rest[1:])
            return format_result(rt.collection(action, **payload))

        if cmd in {"webui", "ui", "观察台", "dashboard"}:
            host = "127.0.0.1"
            port = 8765
            life_dir = rest[0] if rest else None
            suffix = f" --life-dir {life_dir!r}" if life_dir else ""
            return (
                "LifeEngine WebUI / Observatory\n"
                "==============================\n"
                f"启动命令：hermes lifeengine webui --host {host} --port {port}{suffix} --open\n"
                f"打开地址：http://{host}:{port}\n\n"
                "WebUI 会显示：像素小人实时状态、今天/本周日程、Review 列表、资源、梦境、流水。"
            )
        if cmd in {"living", "生活", "节律", "小日子"}:
            action = rest[0] if rest else "summary"
            payload = {"preset": "default"}
            if action in {"day", "daily", "today"}:
                action = "day_rhythm"
            if action in {"inventory", "库存初始化"}:
                action = "init_inventory"
            if action in {"notes", "纸条", "小纸条"}:
                action = "paper_notes"
            if action in {"note", "写纸条"}:
                action = "create_note"
                payload["summary"] = " ".join(rest[1:]) or "我有一件小事想之后告诉你。"
            if action in {"consistency", "canon", "一致性"}:
                action = "consistency"
            if action in {"decompose", "分解"}:
                action = "decompose_abstract"
                if len(rest) > 1:
                    payload["event_id"] = rest[1]
            return format_result(rt.living(action, **payload))
        if cmd in {"run", "推进"}:
            return format_result(rt.tick())
        if cmd in {"backup", "备份"}:
            return format_result(rt.upgrade("export"))
        if cmd in {"schedule", "日程", "calendar", "cal"}:
            period = rest[0] if rest else "today"
            date = None
            if period and period[0].isdigit():
                date = period
                period = "day"
            if period in {"今天", "今日"}:
                period = "today"
            if period in {"明天"}:
                period = "tomorrow"
            if period in {"本周", "一周", "week"}:
                period = "week"
            if period in {"未排期", "待排期"}:
                period = "unscheduled"
            if period in {"说明", "解释"}:
                period = "explain"
            return format_result(rt.schedule(period, date=date))
        if cmd in {"config", "settings", "设定检查", "配置"}:
            action = rest[0] if rest else "summary"
            if action in {"requirements", "spec", "必选项", "suggest_defaults", "apply_default_draft", "defaults", "补全建议"}:
                return format_result(rt.required_settings(action, kind=(rest[1] if len(rest) > 1 else "balanced")))
            if action in {"set", "patch", "补充"}:
                if len(rest) >= 3 and "." in rest[1]:
                    return format_result(rt.required_settings("patch", path=rest[1], value=" ".join(rest[2:])))
                return format_result(rt.required_settings("patch", text=" ".join(rest[1:])))
            return format_result(rt.required_settings(action))
        if cmd in {"review", "inbox", "待办", "查看"}:
            if rest and rest[0] in {"runs", "history"}:
                return format_result(rt.review("runs"))
            if rest and rest[0] in {"get", "explain"} and len(rest) >= 2:
                return format_result(rt.review("get_run", review_run_id=rest[1]))
            if rest and rest[0] in {"dismiss", "resolve"} and len(rest) >= 2:
                return format_result(rt.review("dismiss", item_id=rest[1], reason="/life review dismiss"))
            if rest and rest[0] in {"preview", "plan"} and len(rest) >= 2:
                choice = rest[2] if len(rest) >= 3 else None
                return format_result(rt.review("preview_action", item_id=rest[1], choice=choice, dry_run=True))
            if rest and rest[0] in {"apply", "do", "执行"} and len(rest) >= 2:
                choice = rest[2] if len(rest) >= 3 else None
                return format_result(rt.review("apply", item_id=rest[1], choice=choice))
            if rest and rest[0] in {"actions", "applied"}:
                return format_result(rt.review("action_runs"))
            if rest and rest[0] == "get_action" and len(rest) >= 2:
                return format_result(rt.review("get_action", action_run_id=rest[1]))
            if rest and rest[0] in {"policy", "action_policy"}:
                return format_result(rt.review("policy"))
            if rest and rest[0] in {"batch_preview", "preview_all", "dry_run_all"}:
                section = rest[1] if len(rest) >= 2 else None
                return format_result(rt.review("batch_preview", section=section, dry_run=True))
            if rest and rest[0] in {"apply_all", "apply_safe"}:
                section = rest[1] if len(rest) >= 2 else None
                return format_result(rt.review("apply_all", section=section, safe_only=True))
            if rest and rest[0] in {"batch_runs", "batches"}:
                return format_result(rt.review("batch_runs"))
            if rest and rest[0] == "get_batch" and len(rest) >= 2:
                return format_result(rt.review("get_batch", batch_run_id=rest[1]))
            if rest and rest[0] in {"undo_preview", "preview_undo"} and len(rest) >= 2:
                return format_result(rt.review("undo_preview", action_run_id=rest[1]))
            if rest and rest[0] in {"undo", "rollback"} and len(rest) >= 2:
                return format_result(rt.review("undo", action_run_id=rest[1], reason="slash review undo"))
            if rest and rest[0] in {"batch_undo_preview", "preview_batch_undo"} and len(rest) >= 2:
                return format_result(rt.review("batch_undo_preview", batch_run_id=rest[1]))
            if rest and rest[0] in {"batch_undo", "undo_batch", "rollback_batch"} and len(rest) >= 2:
                return format_result(rt.review("batch_undo", batch_run_id=rest[1], reason="slash review batch undo"))
            if rest and rest[0] in {"undo_runs", "undos"}:
                return format_result(rt.review("undo_runs"))
            if rest and rest[0] == "get_undo" and len(rest) >= 2:
                return format_result(rt.review("get_undo", undo_run_id=rest[1]))
            if rest and rest[0] in {"managed_preview", "agent_preview"}:
                return format_result(rt.review("managed_preview", trigger_source="slash", dry_run=True, force=True))
            if rest and rest[0] in {"managed_run", "agent_run"}:
                return format_result(rt.review("managed_run", trigger_source="slash", force=True))
            if rest and rest[0] in {"managed_runs", "agent_runs"}:
                return format_result(rt.review("managed_runs"))
            if rest and rest[0] == "get_managed_run" and len(rest) >= 2:
                return format_result(rt.review("get_managed_run", managed_run_id=rest[1]))
            if rest and rest[0] in {"managed_state", "agent_state"}:
                return format_result(rt.review("managed_state"))
            if rest and rest[0] in {"managed_acceptance", "acceptance"}:
                return format_result(rt.review("managed_acceptance"))
            if rest and rest[0] in {"managed_acceptance_runs", "acceptance_runs"}:
                return format_result(rt.review("managed_acceptance_runs"))
            if rest and rest[0] in {"get_managed_acceptance", "acceptance_get"} and len(rest) >= 2:
                return format_result(rt.review("get_managed_acceptance", acceptance_run_id=rest[1]))
            if rest and rest[0] in {"managed_stress", "stress"}:
                count = int(rest[1]) if len(rest) >= 2 and rest[1].isdigit() else 25
                return format_result(rt.review("managed_stress", count=count))
            if rest and rest[0] in {"managed_stress_runs", "stress_runs"}:
                return format_result(rt.review("managed_stress_runs"))
            if rest and rest[0] in {"get_managed_stress", "stress_get"} and len(rest) >= 2:
                return format_result(rt.review("get_managed_stress", stress_run_id=rest[1]))
            if rest and rest[0] in {"managed_observability", "observability"}:
                return format_result(rt.review("managed_observability"))
            if rest and rest[0] in {"managed_observability_reports", "observability_reports"}:
                return format_result(rt.review("managed_observability_reports"))
            if rest and rest[0] in {"get_managed_observability", "get_observability"} and len(rest) >= 2:
                return format_result(rt.review("get_managed_observability", report_id=rest[1]))
            if rest and rest[0] in {"managed_readiness", "release_readiness", "readiness"}:
                return format_result(rt.review("managed_release_readiness"))
            if rest and rest[0] in {"managed_readiness_reports", "readiness_reports"}:
                return format_result(rt.review("managed_release_readiness_reports"))
            if rest and rest[0] in {"get_managed_readiness", "get_readiness"} and len(rest) >= 2:
                return format_result(rt.review("get_managed_release_readiness", report_id=rest[1]))
            out = rt.review("summary")
            return out.get("rendered") or format_result(out)
        if cmd in {"policy", "策略", "规则"}:
            if not rest or rest[0] in {"get", "status", "summary"}:
                return format_result(rt.policy("get"))
            if rest[0] in {"explain", "说明"}:
                return format_result(rt.policy("explain"))
            if rest[0] in {"suggest", "suggestions", "review", "建议"}:
                return format_result(rt.policy("suggestions"))
            if rest[0] in {"preset", "profile"} and len(rest) >= 2:
                return format_result(rt.policy("preset", preset=rest[1]))
            if rest[0] in {"reset", "defaults"}:
                return format_result(rt.policy("reset"))
            if rest[0] in {"audits", "history"}:
                return format_result(rt.policy("audits"))
            if rest[0] in {"conflicts", "check", "validate", "冲突"}:
                return format_result(rt.policy("conflicts"))
            if rest[0] in {"conflict_reports", "reports"}:
                return format_result(rt.policy("conflict_reports"))
            if rest[0] in {"export", "导出"}:
                return format_result(rt.policy("export"))
            if rest[0] in {"exports"}:
                return format_result(rt.policy("exports"))
            if rest[0] in {"import"} and len(rest) >= 2:
                return format_result(rt.policy("import", path=rest[1], apply=("--apply" in rest or "apply" in rest)))
            if rest[0] in {"acceptance", "验收"}:
                return format_result(rt.policy("acceptance"))
            if rest[0] in {"acceptance_runs"}:
                return format_result(rt.policy("acceptance_runs"))
            if rest[0] in {"acceptance_get"} and len(rest) >= 2:
                return format_result(rt.policy("acceptance_get", acceptance_run_id=rest[1]))
            return format_result(rt.policy("get"))
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
            if action in {"sleep_reply_dream_acceptance", "srd_acceptance", "sleep_dream_acceptance"}:
                return format_result(rt.upgrade("sleep_reply_dream_acceptance"))
            if action in {"sleep_reply_dream_acceptance_runs", "srd_acceptance_runs"}:
                return format_result(rt.upgrade("sleep_reply_dream_acceptance_runs"))
            if action in {"sleep_reply_dream_acceptance_get", "srd_acceptance_get"} and len(rest) > 1:
                return format_result(rt.upgrade("sleep_reply_dream_acceptance_get", acceptance_run_id=rest[1]))
            if action in {"sleep_autonomy_execution_acceptance", "sae_acceptance", "sleep_execution_acceptance"}:
                return format_result(rt.upgrade("sleep_autonomy_execution_acceptance"))
            if action in {"sleep_autonomy_execution_acceptance_runs", "sae_acceptance_runs"}:
                return format_result(rt.upgrade("sleep_autonomy_execution_acceptance_runs"))
            if action in {"sleep_autonomy_execution_acceptance_get", "sae_acceptance_get"} and len(rest) > 1:
                return format_result(rt.upgrade("sleep_autonomy_execution_acceptance_get", acceptance_run_id=rest[1]))
            if action in {"sleep_reply_dream_conversation_acceptance", "crd_acceptance", "conversation_acceptance", "srd_conversation_acceptance"}:
                return format_result(rt.upgrade("sleep_reply_dream_conversation_acceptance"))
            if action in {"sleep_reply_dream_conversation_acceptance_runs", "crd_acceptance_runs", "srd_conversation_acceptance_runs"}:
                return format_result(rt.upgrade("sleep_reply_dream_conversation_acceptance_runs"))
            if action in {"sleep_reply_dream_conversation_acceptance_get", "crd_acceptance_get", "srd_conversation_acceptance_get"} and len(rest) > 1:
                return format_result(rt.upgrade("sleep_reply_dream_conversation_acceptance_get", acceptance_run_id=rest[1]))
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
            if rest[0] in {"sleep", "sleep_context"}:
                return format_result(rt.autonomy("sleep_context"))
            if rest[0] in {"sleep_adjustments", "adjustments"}:
                return format_result(rt.autonomy("sleep_adjustments"))
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
            if rest[0] in {"sleep_context", "sleep"}:
                return format_result(rt.execution("sleep_context"))
            if rest[0] in {"sleep_adjustments", "adjustments"}:
                return format_result(rt.execution("sleep_adjustments"))
            if rest[0] == "get" and len(rest) >= 2:
                return format_result(rt.execution("get", decision_id=rest[1]))
            if rest[0] in {"run", "simulate", "execute"}:
                payload = {}
                if len(rest) >= 2:
                    payload["schedule_block_id"] = rest[1]
                return format_result(rt.execution(rest[0], **payload))
        if cmd in {"sleep", "睡眠", "睡觉"}:
            if not rest or rest[0] in {"status", "state"}:
                return format_result(rt.sleep_tool("status"))
            if rest[0] in {"plan", "plan_day"}:
                return format_result(rt.sleep_tool("plan_day"))
            if rest[0] == "nap":
                return format_result(rt.sleep_tool("nap", planned_start=(rest[1] if len(rest) > 1 else None), planned_end=(rest[2] if len(rest) > 2 else None)))
            if rest[0] in {"start", "sleep"}:
                payload = {"sleep_plan_id": rest[1]} if len(rest) > 1 else {}
                return format_result(rt.sleep_tool("start", **payload))
            if rest[0] in {"wake", "end"}:
                payload = {"sleep_session_id": rest[1]} if len(rest) > 1 else {}
                return format_result(rt.sleep_tool("wake", **payload))
            if rest[0] == "plans":
                return format_result(rt.sleep_tool("plans"))
            if rest[0] == "sessions":
                return format_result(rt.sleep_tool("sessions"))

        if cmd in {"dream", "梦", "做梦"}:
            if not rest or rest[0] in {"status", "state"}:
                return format_result(rt.dream("status"))
            if rest[0] in {"run", "cycle", "dream"}:
                payload = {}
                if len(rest) >= 2:
                    payload["sleep_session_id"] = rest[1]
                return format_result(rt.dream("run", **payload))
            if rest[0] in {"entries", "dreams"}:
                return format_result(rt.dream("entries"))
            if rest[0] in {"findings", "audit"}:
                return format_result(rt.dream("findings" if rest[0] == "findings" else "audit"))
            if rest[0] in {"get", "explain"} and len(rest) >= 2:
                return format_result(rt.dream("get", dream_run_id=rest[1]))

        if cmd in {"call", "叫醒", "紧急"}:
            return format_result(rt.call(reason="/life call", message_text=" ".join(rest) if rest else None, user_id=None))
        if cmd in {"reply", "回复门", "replygate"}:
            if not rest or rest[0] in {"status", "state"}:
                return format_result(rt.reply("status"))
            if rest[0] in {"list", "delayed"}:
                return format_result(rt.reply("list"))
            if rest[0] in {"release", "释放"}:
                return format_result(rt.reply("release", reason="/life reply release"))
            if rest[0] in {"doctor", "check"}:
                return format_result(rt.reply("doctor"))
            if rest[0] in {"assess", "gate"}:
                return format_result(rt.reply("assess", message_text=" ".join(rest[1:])))

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
