"""LifeEngine Hermes plugin.

Directory plugin layout for current Hermes main:
  ~/.hermes/plugins/lifeengine/plugin.yaml
  ~/.hermes/plugins/lifeengine/__init__.py

Hermes calls register(ctx) once during plugin discovery.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import hooks, schemas, tools
from .cli import handle_cli, setup_cli_parser, slash_life

logger = logging.getLogger(__name__)


def register(ctx):
    """Register LifeEngine tools, hooks, slash command, CLI command, and skill."""
    toolset = "lifeengine"
    for name, schema, handler, desc, emoji in [
        ("life_status", schemas.LIFE_STATUS, tools.life_status, "Read LifeEngine status", "🫀"),
        ("life_doctor", schemas.LIFE_DOCTOR, tools.life_doctor, "Run LifeEngine health checks", "🩺"),
        ("life_upgrade", schemas.LIFE_UPGRADE, tools.life_upgrade, "Install, upgrade, backup, and maintenance checks", "🧰"),
        ("life_control", schemas.LIFE_CONTROL, tools.life_control, "Pause/resume/setup/module control", "🕹️"),
        ("life_setup", schemas.LIFE_SETUP, tools.life_setup, "Write CanonDraft settings", "🧬"),
        ("life_commit", schemas.LIFE_COMMIT, tools.life_commit, "Commit CanonDraft or LifeOps", "✅"),
        ("life_resource", schemas.LIFE_RESOURCE, tools.life_resource, "Resource registry and ledger", "🔋"),
        ("life_event", schemas.LIFE_EVENT, tools.life_event, "Event/action/result/schedule operations", "📅"),
        ("life_memory", schemas.LIFE_MEMORY, tools.life_memory, "Structured/FTS/vector memory", "🧠"),
        ("life_tick", schemas.LIFE_TICK, tools.life_tick, "Manual heartbeat tick", "💓"),
        ("life_diary", schemas.LIFE_DIARY, tools.life_diary, "Diary entries from committed life", "📓"),
        ("life_trace", schemas.LIFE_TRACE, tools.life_trace, "Trace, journal, audit inspection", "🔎"),
        ("life_final_gate", schemas.LIFE_FINAL_GATE, tools.life_final_gate, "FinalGate reports and repair suggestions", "🚧"),
        ("life_truth", schemas.LIFE_TRUTH, tools.life_truth, "Canon truth-source resolver", "🧭"),
        ("life_confirmation", schemas.LIFE_CONFIRMATION, tools.life_confirmation, "User Life confirmation flow", "📝"),
        ("life_inventory", schemas.LIFE_INVENTORY, tools.life_inventory, "Entity-resource inventory and meals", "🎒"),
        ("life_goal", schemas.LIFE_GOAL, tools.life_goal, "Goals, life arcs, decomposition, and reflection", "🎯"),
        ("life_autonomy", schemas.LIFE_AUTONOMY, tools.life_autonomy, "Autonomy planner decisions and runs", "🧭"),
        ("life_proactive", schemas.LIFE_PROACTIVE, tools.life_proactive, "Proactive intent and outbox state", "📣"),
        ("life_execution", schemas.LIFE_EXECUTION, tools.life_execution, "Narrative execution simulator and serendipity", "🎲"),
    ]:
        ctx.register_tool(name=name, toolset=toolset, schema=schema, handler=handler, description=desc, emoji=emoji)

    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
    ctx.register_hook("transform_llm_output", hooks.transform_llm_output)
    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("on_session_end", hooks.on_session_end)

    ctx.register_command("life", slash_life, description="Manage LifeEngine", args_hint="help|status|setup|commit|pause|resume|run|review|doctor|backup|advanced")
    ctx.register_cli_command(
        name="lifeengine",
        help="Manage embedded LifeEngine",
        setup_fn=setup_cli_parser,
        handler_fn=handle_cli,
        description="Control LifeEngine state, Canon, heartbeat, resources, and traces.",
    )

    skill_md = Path(__file__).parent / "skills" / "lifeengine" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("lifeengine", skill_md, description="How to use LifeEngine tools safely")

    logger.info("LifeEngine plugin registered")
