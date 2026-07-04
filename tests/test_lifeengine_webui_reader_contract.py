"""WebUI reader 的 schema 覆盖合同测试。

这个测试不验证某个具体页面渲染，而是把当前 schema 表和 WebUI read layer
之间的覆盖关系固定下来。以后新增表时，维护者必须明确选择：读进 Observatory，
或把它作为已知 backlog / infra 表写进这里。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

from lifeengine.runtime import LifeEngineRuntime


REPO_ROOT = Path(__file__).resolve().parents[1]
READER_PATH = REPO_ROOT / "webui" / "reader.py"


# 这些表保存角色生活、世界、关系、计划、记忆或资源状态。它们应该被 WebUI 读取；
# 如果当前还没有读面，必须在 KNOWN_UNSURFACED 里留下一行 backlog 原因。
LIFE_NARRATIVE_TABLES = {
    "action_state_transitions",
    "actions",
    "agent_loadout",
    "agent_opinions",
    "agent_realtime_state",
    "agent_state_snapshots",
    "agent_user_proactive_state",
    "autonomy_sleep_adjustments",
    "call_overrides",
    "campaign_phase_occurrences",
    "campaigns",
    "collection_item_aliases",
    "collection_item_assets",
    "collection_items",
    "collection_purchase_chains",
    "collection_usage_history",
    "conversation_activity_judgments",
    "delayed_replies",
    "delayed_reply_digests",
    "diary_entries",
    "dream_entries",
    "event_decompositions",
    "event_dependencies",
    "event_goal_links",
    "event_state_transitions",
    "events",
    "execution_sleep_adjustments",
    "goal_milestones",
    "goal_progress_entries",
    "goals",
    "item_collections",
    "life_arcs",
    "life_author_runs",
    "life_reflections",
    "life_rhythm_items",
    "meal_records",
    "memories",
    "outfit_plans",
    "outfit_presets",
    "outfit_resolutions",
    "outfit_snapshots",
    "persona_drift_log",
    "persona_traits",
    "proactive_deliveries",
    "proactive_intents",
    "proactive_outbox",
    "relationship_notes",
    "reply_gate_decisions",
    "reply_gate_recoveries",
    "reputation_accounts",
    "reputation_events",
    "resource_accounts",
    "resource_definitions",
    "resource_ledger",
    "resource_reservations",
    "results",
    "rumor_exposures",
    "rumors",
    "schedule_block_executions",
    "schedule_block_state_transitions",
    "schedule_blocks",
    "serendipity_events",
    "sleep_day_states",
    "sleep_interruptions",
    "sleep_plans",
    "sleep_recovery_plans",
    "sleep_session_state_transitions",
    "sleep_sessions",
    "social_edges",
    "social_evaluations",
    "social_request_transitions",
    "social_requests",
    "thoughts",
    "user_activity_spans",
    "venture_occurrences",
    "venture_opportunity_arrivals",
    "venture_restock_orders",
    "ventures",
    "world_affiliations",
    "world_chronicle_events",
    "world_conditions",
    "world_entities",
    "world_faction_presence",
    "world_lore_entries",
    "world_places",
    "world_profiles",
    "world_regions",
    "world_routes",
    "worldview_slot_definitions",
}


# 这些表是迁移、canon/control、trace、receipt、doctor、测试验收、发布/安装、
# LLM 调用审计、FTS/vec shadow 表或其它运行维护账本，不要求进入生活叙事读面。
INFRA_ADMIN_TABLES = {
    "audit_log",
    "autonomy_decisions",
    "behavior_mapping_runs",
    "behavior_mapping_sources",
    "behavior_mappings",
    "canon_consistency_reports",
    "canon_drafts",
    "canon_migrations",
    "canon_versions",
    "collection_asset_checks",
    "collection_maintenance_runs",
    "collection_rule_presets",
    "command_surface_profiles",
    "commit_receipt_facts",
    "commit_receipts",
    "controls",
    "cron_heartbeat_tests",
    "db_backups",
    "dream_repair_policies",
    "dream_repair_runs",
    "dream_runs",
    "execution_decisions",
    "failed_lifeops_audits",
    "final_gate_feedback_queue",
    "final_gate_reports",
    "heartbeat_runs",
    "human_review_action_policies",
    "human_review_action_runs",
    "human_review_batch_items",
    "human_review_batch_runs",
    "human_review_items",
    "human_review_managed_loop_runs",
    "human_review_managed_loop_state",
    "human_review_runs",
    "human_review_undo_items",
    "human_review_undo_runs",
    "install_checks",
    "inter_agent_outbox",
    "life_branches",
    "life_invariant_checks",
    "life_journal",
    "life_ops",
    "life_required_setting_checks",
    "life_rhythm_runs",
    "life_transactions",
    "living_inventory_preset_runs",
    "maintenance_runs",
    "memory_fts",
    "memory_fts_config",
    "memory_fts_content",
    "memory_fts_data",
    "memory_fts_docsize",
    "memory_fts_idx",
    "memory_vec",
    "memory_vec_chunks",
    "memory_vec_info",
    "memory_vec_rowids",
    "memory_vec_vector_chunks00",
    "nightly_check_findings",
    "outfit_resolver_runs",
    "owners",
    "package_manifests",
    "proactive_evaluations",
    "profile_exports",
    "profile_imports",
    "prompt_context_runs",
    "prompt_context_session_mounts",
    "resource_reconcile_checks",
    "resource_reconciliations",
    "restore_staging",
    "schema_migrations",
    "sleep_autonomy_execution_acceptance_runs",
    "sleep_autonomy_execution_acceptance_scenarios",
    "sleep_doctor_findings",
    "sleep_reply_dream_conversation_acceptance_runs",
    "sleep_reply_dream_conversation_acceptance_scenarios",
    "sleep_reply_dream_policies",
    "sleep_reply_dream_policy_acceptance_runs",
    "sleep_reply_dream_policy_acceptance_scenarios",
    "sleep_reply_dream_policy_audits",
    "sleep_reply_dream_policy_conflict_reports",
    "sleep_reply_dream_policy_exports",
    "sleep_reply_dream_policy_imports",
    "sleep_reply_dream_policy_suggestions",
    "social_projection_runs",
    "stale_event_cleanup_runs",
    "trace_coverage_reports",
    "trace_integrity_checks",
    "trace_runs",
    "trace_spans",
    "truth_source_cache",
    "truth_source_reads",
    "turn_commits",
    "upgrade_runs",
    "user_confirmations",
    "v010_release_notes",
    "wake_jobs",
}


# 当前已经写入但 WebUI 还没有生活叙事读面的表。每个值都是 backlog 原因；
# 表一旦被 reader.py 读取，就必须从这里移除，避免 allow-list 变成旧债仓库。
KNOWN_UNSURFACED = {
    "agent_state_snapshots": "mode history exists, but Observatory only reads current realtime state",
    "agent_user_proactive_state": "companion cooldown and budget state has no panel beyond intents/outbox",
    "autonomy_sleep_adjustments": "sleep/autonomy adjustment ledger lacks a human summary view",
    "call_overrides": "call interruption records are not shown outside delayed reply flow",
    "collection_purchase_chains": "purchase chain history is not shown in the collection board",
    "collection_usage_history": "closet and collection use history is not shown in the collection board",
    "conversation_activity_judgments": "user activity classification has no Observatory timeline yet",
    "delayed_reply_digests": "digest summaries are written while delayed reply panel reads raw pending rows only",
    "event_decompositions": "plan breakdown structure is not surfaced in event detail yet",
    "event_dependencies": "event dependency graph is not surfaced in schedule or event detail yet",
    "event_goal_links": "goal linkage from events is not shown until goals surface lands",
    "life_arcs": "long-lived arcs are written but no arcs/goals panel exists yet",
    "life_author_runs": "generation-authoring audit (which agent authored what) has no Observatory surface yet",
    "life_reflections": "reflection records are not in the life feed beyond self-narrative memories",
    "outfit_resolutions": "resolver decisions are not displayed in collections or outfit UI",
    "outfit_snapshots": "worn outfit history has no timeline or card surface yet",
    "proactive_deliveries": "delivery attempts are not shown; only intents and outbox are read",
    "reply_gate_decisions": "reply-gate decision history has no communication panel yet",
    "reply_gate_recoveries": "reply-gate recovery notices are not shown in the Observatory",
    "resource_reservations": "reserved resource holds are not shown with resource accounts or ledger",
    "schedule_block_executions": "execution history is not shown next to schedule blocks",
    "sleep_interruptions": "sleep interruption records are not shown in sleep/day state UI",
    "sleep_plans": "planned sleep objects are not shown; only latest day aggregate is read",
    "sleep_recovery_plans": "recovery plans have no sleep panel yet",
    "sleep_session_state_transitions": "sleep transition history is not shown in sleep UI",
    "sleep_sessions": "actual sleep sessions are not shown; only latest sleep day aggregate is read",
    "thoughts": "private thoughts are written but no safe Observatory surface exists yet",
    "user_activity_spans": "user activity spans are not displayed in the human/relationship surface",
    "venture_occurrences": "venture materialized occurrences are not shown in campaign or world panels",
    "venture_opportunity_arrivals": "incoming venture opportunities have no Observatory surface yet",
    "venture_restock_orders": "venture supply orders have no shop or venture panel yet",
    "ventures": "recurring venture definitions have no Observatory surface yet",
}


SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([\"'`]?)([A-Za-z_][A-Za-z0-9_]*)\1",
    re.IGNORECASE,
)
DELETE_TABLE_RE = re.compile(
    r"\bDELETE\s+FROM\s+([\"'`]?)([A-Za-z_][A-Za-z0-9_]*)\1",
    re.IGNORECASE,
)
TABLE_EXISTS_RE = re.compile(
    r"_table_exists\(\s*conn\s*,\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\)"
)
HELPER_TABLE_ARG_RE = re.compile(
    r"\b_?q\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*(?:,|\))"
)
ENGINE_SURFACE_REGISTRY = "SURFACED_VIA_ENGINE"


def _schema_tables(tmp_path: Path, monkeypatch) -> set[str]:
    """用当前 runtime 建一个隔离库，并从 sqlite_master 动态读取真实表名。

    输入是 pytest 的 tmp_path 和 monkeypatch；输出是当前 schema 的非 sqlite_ 表。
    调用方是本合同测试。副作用仅限临时 HERMES_HOME 下创建 SQLite 文件，失败时由
    runtime 初始化或 sqlite 查询异常直接暴露。
    """
    home = tmp_path / "hermes_home_reader_contract"
    monkeypatch.setenv("HERMES_HOME", str(home))
    rt = LifeEngineRuntime()
    try:
        return {
            str(row[0])
            for row in rt.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        rt.close()


def _engine_surface_registry(source: str) -> dict[str, str]:
    """读取 reader.py 中显式声明的 engine read-model 覆盖表。

    输入是 reader.py 源码；输出为 table -> dotted engine function。调用方是
    schema 合同测试的静态扫描。函数不执行 reader，只解析字面量注册表；注册表缺失
    或格式错误时直接断言失败，避免 SQL 迁移后靠隐式约定绕过覆盖检查。
    """
    tree = ast.parse(source, filename=str(READER_PATH))
    registry_node: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == ENGINE_SURFACE_REGISTRY:
                registry_node = node.value
                break
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == ENGINE_SURFACE_REGISTRY for target in node.targets):
                registry_node = node.value
                break
    if registry_node is None:
        return {}
    raw = ast.literal_eval(registry_node)
    assert isinstance(raw, dict), f"{ENGINE_SURFACE_REGISTRY} must be a literal dict"
    registry = {str(table): str(dotted) for table, dotted in raw.items()}
    bad = {
        table: dotted
        for table, dotted in registry.items()
        if not table.strip() or "." not in dotted
    }
    assert not bad, f"{ENGINE_SURFACE_REGISTRY} entries must be table -> dotted function path: {bad}"
    return registry


def _source_referenced_tables(source: str, schema_tables: set[str]) -> set[str]:
    """从一段源码中提取直接 SQL 提到的当前 schema 表。

    输入是函数或 reader 源码以及动态 schema 表集合；输出是 SQL/guard/helper
    识别到的表名交集。调用方同时用于 reader.py 和注册的 engine read-model 函数。
    它只认真实源码引用，不读取 allow-list，保证新生活表仍需被显式 surfacing。
    """
    names: set[str] = set()
    for pattern in (SQL_TABLE_RE, DELETE_TABLE_RE):
        names.update(match.group(2) for match in pattern.finditer(source))
    names.update(match.group(1) for match in TABLE_EXISTS_RE.finditer(source))
    names.update(match.group(1) for match in HELPER_TABLE_ARG_RE.finditer(source))
    return names & schema_tables


def _resolve_engine_function(dotted_path: str):
    """把注册表中的 dotted path 解析为真实 engine read-model 函数。

    输入是形如 `lifeengine.world_model.list_profiles` 的路径；输出是可 inspect 的
    函数对象。调用方是合同测试。解析失败直接断言失败，使无效注册不会静默变成
    surfaced。
    """
    module_name, _, attr = dotted_path.rpartition(".")
    assert module_name and attr, f"Invalid engine read-model path: {dotted_path}"
    module = importlib.import_module(module_name)
    fn = getattr(module, attr, None)
    assert callable(fn), f"Engine read-model is not callable: {dotted_path}"
    return fn


def _engine_referenced_tables(registry: dict[str, str], schema_tables: set[str]) -> set[str]:
    """扫描注册的 engine read-model 函数源码，返回它们实际读取的表。

    输入是 reader 的 table -> engine function 注册表和动态 schema 表集合；输出是
    这些函数源码中直接 SQL 读取到的 schema 表。调用方是 reader 覆盖扫描。每个
    注册项都必须指向会读取该表的函数，否则断言失败；这让注册表是可审计的合同，
    不是把 missing 表涂绿的橡皮章。
    """
    out: set[str] = set()
    mismatches: dict[str, str] = {}
    for table, dotted_path in registry.items():
        fn = _resolve_engine_function(dotted_path)
        source = inspect.getsource(fn)
        function_tables = _source_referenced_tables(source, schema_tables)
        if table not in function_tables:
            mismatches[table] = f"{dotted_path} reads {_format_names(function_tables)}"
        out.update(function_tables)
    assert not mismatches, (
        f"{ENGINE_SURFACE_REGISTRY} claims tables that are not read by their engine functions: {mismatches}"
    )
    return out


def _reader_referenced_tables(schema_tables: set[str]) -> set[str]:
    """静态扫描 reader.py，提取 WebUI read layer 明确提到的 schema 表。

    输入是动态 schema 表集合；输出是 reader SQL、_table_exists guard、本地查询
    helper 参数和显式注册的 engine read-model 中出现的表名。调用方是合同测试。
    函数只读源码文件和 engine 函数源码，不执行 reader。
    """
    source = READER_PATH.read_text(encoding="utf-8")
    registry = _engine_surface_registry(source)
    names = _source_referenced_tables(source, schema_tables)
    names.update(_engine_referenced_tables(registry, schema_tables))
    return names & schema_tables


def _format_names(names: set[str]) -> str:
    """把失败集合格式化成稳定顺序，方便维护者直接复制处理。"""
    return ", ".join(sorted(names)) if names else "(none)"


def test_webui_reader_schema_coverage_contract(tmp_path: Path, monkeypatch) -> None:
    """强制 schema 新增和 WebUI 读取覆盖之间必须有显式决策。"""
    schema_tables = _schema_tables(tmp_path, monkeypatch)
    reader_tables = _reader_referenced_tables(schema_tables)

    classified_tables = LIFE_NARRATIVE_TABLES | INFRA_ADMIN_TABLES
    overlapping_classification = LIFE_NARRATIVE_TABLES & INFRA_ADMIN_TABLES
    stale_classification = classified_tables - schema_tables
    unclassified_schema = schema_tables - classified_tables
    stale_known_unsurfaced = set(KNOWN_UNSURFACED) - schema_tables
    known_outside_life = set(KNOWN_UNSURFACED) - LIFE_NARRATIVE_TABLES
    known_already_surfaced = set(KNOWN_UNSURFACED) & reader_tables
    known_without_reason = {
        table
        for table, reason in KNOWN_UNSURFACED.items()
        if not reason.strip() or "\n" in reason
    }
    missing_reader_surface = LIFE_NARRATIVE_TABLES - reader_tables - set(KNOWN_UNSURFACED)

    assert not overlapping_classification, (
        "Schema tables must be classified in exactly one bucket. "
        f"Remove duplicate life/infra entries: {_format_names(overlapping_classification)}"
    )
    assert not stale_classification, (
        "The reader contract classifies tables that no longer exist in the live schema. "
        f"Remove or rename these entries: {_format_names(stale_classification)}"
    )
    assert not unclassified_schema, (
        "New schema tables are missing from the WebUI reader contract. "
        f"Unclassified tables: {_format_names(unclassified_schema)}. "
        "If a table is life/narrative state, add it to LIFE_NARRATIVE_TABLES and either "
        "surface it in webui/reader.py or document it in KNOWN_UNSURFACED with a reason. "
        "If it is migration/control/trace/receipt/index/admin state, add it to INFRA_ADMIN_TABLES."
    )
    assert not stale_known_unsurfaced, (
        "KNOWN_UNSURFACED contains tables removed from the live schema. "
        f"Remove stale backlog entries: {_format_names(stale_known_unsurfaced)}"
    )
    assert not known_outside_life, (
        "KNOWN_UNSURFACED may only document life/narrative tables. "
        f"Move these to LIFE_NARRATIVE_TABLES or remove them: {_format_names(known_outside_life)}"
    )
    assert not known_already_surfaced, (
        "KNOWN_UNSURFACED has stale entries already referenced by webui/reader.py. "
        f"Remove these from the allow-list: {_format_names(known_already_surfaced)}"
    )
    assert not known_without_reason, (
        "KNOWN_UNSURFACED entries need a one-line reason. "
        f"Fill in reasons for: {_format_names(known_without_reason)}"
    )
    assert not missing_reader_surface, (
        "Life/narrative tables are neither read by webui/reader.py nor explicitly backlogged. "
        f"Missing reader surface: {_format_names(missing_reader_surface)}. "
        "Add reader SQL/guards so the Observatory can show them, or add each table to "
        "KNOWN_UNSURFACED with a one-line reason if it is an accepted current gap."
    )
