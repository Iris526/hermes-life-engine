"""LifeEngine WebUI reader 的行为保持 golden-snapshot 测试。

本测试只覆盖 read layer 的当前 JSON 合同，不修改生产代码。它用真实 demo seed
创建凛与青的双 agent 世界，通过 FastAPI TestClient 捕获前端可见 `/api/*`
读接口，再用 direct reader 调用补齐 snapshot 间接使用但未单独暴露的读面。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lifeengine.scripts.seed_demo import AGENT_B_ID, DEMO_DATE, OWNER_ID, seed_demo
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "webui_reader"
REGEN_GOLDEN = os.getenv("HERMES_REGEN_GOLDEN") == "1"
OWNERS = (("agent", OWNER_ID), ("agent", AGENT_B_ID))

ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OPAQUE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{8,}$")
EMBEDDED_OPAQUE_ID_RE = re.compile(r"(?<![A-Za-z0-9])([a-z][a-z0-9_]*_[0-9a-f]{8,})(?![A-Za-z0-9])")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
HEX_HASH_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)
CANON_ID_RE = re.compile(r"^canon_[0-9a-f]{8,}$", re.I)
STABLE_ID_VALUES = {
    "",
    "agent",
    "default-agent",
    "agent-qing",
    "anonymous-user",
    "u1",
    "world",
    "WORLD_AUDIENCE",
    "__world__",
}


@pytest.fixture(scope="session")
def seeded_demo_db(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """为 golden 测试创建一次 session 级真实 demo 数据库。

    输入来自 pytest 的 session 临时目录工厂；输出包含 HERMES_HOME 与 SQLite DB
    路径。调用方是本模块所有 golden 捕获；副作用是临时设置 HERMES_HOME 调用
    `scripts/seed_demo.py` 的真实 seed，并写入一个固定 workspace 文档，让
    workspace docs/file 读面可被稳定覆盖。函数结束 seed 后恢复外层环境变量，
    避免污染同一 worker 内的其它测试。
    """
    home = tmp_path_factory.mktemp("webui_reader_golden_home")
    previous_home = os.environ.get("HERMES_HOME")
    try:
        seed_demo(home, reset=True)
        _stabilize_trace_created_at(home / "lifeengine" / "lifeengine.db")
        (home / "AGENTS.md").write_text(
            "# Golden Workspace\n\nLifeEngine WebUI reader golden fixture.\n",
            encoding="utf-8",
        )
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
    return {"home": home, "db": home / "lifeengine" / "lifeengine.db"}


def _stabilize_trace_created_at(db_path: Path) -> None:
    """稳定 demo seed 中由 SQLite now 默认值生成的 trace 时间。

    输入是临时 SQLite DB 路径；输出为空。调用方是 session 级 seed fixture；副作用
    只限测试临时 DB 的 infra trace/transaction 表。seed_demo 的生活叙事时间已经
    固定，但这些审计表的 `created_at` 由 SQLite 秒级 now 生成，xdist 下同秒 tie
    会让 `ORDER BY created_at` 的 trace surface 不稳定。这里按 rowid 写入固定递增
    时间，保留插入顺序和 reader 行为面，同时让 golden 可重复。
    """
    tables = ("life_transactions", "life_ops", "commit_receipts", "life_journal")
    with sqlite3.connect(db_path) as conn:
        for table in tables:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "created_at" not in columns:
                continue
            rowids = [row[0] for row in conn.execute(f"SELECT rowid FROM {table} ORDER BY rowid").fetchall()]
            for index, rowid in enumerate(rowids):
                conn.execute(
                    f"UPDATE {table} SET created_at=? WHERE rowid=?",
                    (f"2026-07-04 00:{index // 60:02d}:{index % 60:02d}", rowid),
                )


@pytest.fixture()
def demo_env(seeded_demo_db: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """把当前测试临时切到 seeded demo 环境。

    输入是 session 级 seeded DB 与 pytest monkeypatch；输出沿用 seeded DB 信息。
    调用方是捕获函数和断言测试；副作用只在当前测试内设置 HERMES_HOME 并切换
    cwd 到 demo home，使 workspace reader 不会扫描开发机真实项目目录。失败处理
    交给 monkeypatch 自动回滚，保证默认快测套件互不串环境。
    """
    monkeypatch.setenv("HERMES_HOME", str(seeded_demo_db["home"]))
    monkeypatch.chdir(seeded_demo_db["home"])
    return seeded_demo_db


def _get_json(client: TestClient, path: str, params: dict[str, Any] | None = None) -> Any:
    """执行一个 WebUI GET endpoint 并返回 JSON。

    输入是 TestClient、路径和可选 query 参数；输出是 response JSON。调用方式是
    golden 捕获过程中的同步只读请求；副作用仅限 FastAPI app 读取临时 SQLite。
    若 endpoint 不是 200，会直接断言失败，避免把错误页写进 golden。
    """
    response = client.get(path, params=params or {})
    assert response.status_code == 200, f"{path} failed: {response.status_code} {response.text}"
    return response.json()


def _post_json(client: TestClient, path: str, payload: dict[str, Any]) -> Any:
    """执行一个 WebUI POST endpoint 并返回 JSON。

    输入是 TestClient、路径和 JSON body；输出是 response JSON。调用方只用它切换
    WebUI selected owner 后读取 roster/owners，不触发 runtime action。副作用只在
    app 内存 state 中更新 owner，失败时用断言暴露 endpoint 响应。
    """
    response = client.post(path, json=payload)
    assert response.status_code == 200, f"{path} failed: {response.status_code} {response.text}"
    return response.json()


def _first_item_id(payload: Any) -> str | None:
    """从常见 WebUI list payload 中取第一条 id。

    输入是 endpoint 或 reader 返回值；输出是第一条 item 的 id 或 None。调用方用
    它选择 seeded DB 中真实存在的 event/dream/trace 对象去覆盖 detail endpoint。
    函数只读内存结构；空列表或缺 id 时返回 None，让捕获结果显式记录 skipped。
    """
    items = payload.get("items") if isinstance(payload, dict) else payload
    if isinstance(items, list) and items:
        value = items[0].get("id") if isinstance(items[0], dict) else None
        return str(value) if value else None
    return None


def _capture_owner_surfaces(client: TestClient, reader: LifeEngineReader, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """捕获单个 owner 的 WebUI reader 读面。

    输入是 TestClient、direct reader 和 owner 标识；输出是按 surface 名分组的
    JSON-like dict。调用方式以真实 `/api/*` GET 为主，并补 direct reader 方法。
    副作用仅有 `/api/owner` 更新 TestClient 内存选中 owner；数据库始终只读。
    """
    owner = {"owner_kind": owner_kind, "owner_id": owner_id}
    _post_json(client, "/api/owner", owner)

    surfaces: dict[str, Any] = {
        "api_owners_after_select": _get_json(client, "/api/owners"),
        "api_agents_after_select": _get_json(client, "/api/agents"),
        "api_snapshot": _get_json(client, "/api/snapshot", {**owner, "period": "day", "date": DEMO_DATE}),
        "api_state": _get_json(client, "/api/state", owner),
        "api_schedule": _get_json(client, "/api/schedule", {**owner, "period": "day", "date": DEMO_DATE}),
        "api_schedule_open": _get_json(
            client,
            "/api/schedule",
            {**owner, "period": "day", "date": DEMO_DATE, "include_completed": "false"},
        ),
        "api_events": _get_json(client, "/api/events", owner),
        "api_review": _get_json(client, "/api/review", owner),
        "api_resources": _get_json(client, "/api/resources", owner),
        "api_dreams": _get_json(client, "/api/dreams", owner),
        "api_campaigns": _get_json(client, "/api/campaigns", owner),
        "api_goals": _get_json(client, "/api/goals", owner),
        "api_rhythm": _get_json(client, "/api/rhythm", {**owner, "date": DEMO_DATE}),
        "api_feed": _get_json(client, "/api/feed", owner),
        "api_feed_limit3": _get_json(client, "/api/feed", {**owner, "limit": 3}),
        "api_inner_life": _get_json(client, "/api/inner_life", owner),
        "api_relationship": _get_json(client, "/api/relationship", owner),
        "api_social_world": _get_json(client, "/api/social_world", owner),
        "api_world_model": _get_json(client, "/api/world_model", owner),
    }

    feed_cursor = surfaces["api_feed_limit3"].get("next_cursor")
    surfaces["api_feed_after_cursor"] = (
        _get_json(client, "/api/feed", {**owner, "before": feed_cursor, "limit": 3})
        if feed_cursor
        else {"skipped": "no next_cursor"}
    )

    event_id = _first_item_id(surfaces["api_events"])
    dream_id = _first_item_id(surfaces["api_dreams"])
    state = reader.realtime_state(owner_kind, owner_id)
    current_event = reader.current_event(owner_kind, owner_id, state)
    surfaces.update({
        "api_event_detail": _get_json(client, f"/api/event/{event_id}") if event_id else {"skipped": "no event id"},
        "api_dream_detail": _get_json(client, f"/api/dream/{dream_id}") if dream_id else {"skipped": "no dream id"},
        "api_trace_explain_event": (
            _get_json(client, f"/api/trace/explain/{event_id}") if event_id else {"skipped": "no event id"}
        ),
        "api_trace_explain_dream": (
            _get_json(client, f"/api/trace/explain/{dream_id}") if dream_id else {"skipped": "no dream id"}
        ),
        "direct_control": reader.control(owner_kind, owner_id),
        "direct_latest_sleep_day": reader.latest_sleep_day(owner_kind, owner_id),
        "direct_current_event": current_event,
        "direct_identity": reader.identity(owner_kind, owner_id),
        "direct_clock": reader.clock(owner_kind, owner_id),
        "direct_meals_today": reader.meals_today(owner_kind, owner_id),
        "direct_persona": reader.persona(owner_kind, owner_id),
        "direct_delayed_replies": reader.delayed_replies(owner_kind, owner_id),
        "direct_proactive": reader.proactive(owner_kind, owner_id),
        "direct_collections": reader.collections(owner_kind, owner_id),
        "direct_doctor_latest": reader.doctor_latest(owner_kind, owner_id),
    })
    return surfaces


def _capture_global_surfaces(client: TestClient, reader: LifeEngineReader, demo_home: Path) -> dict[str, Any]:
    """捕获不属于单一 owner 的 WebUI reader 读面。

    输入是 TestClient、direct reader 和 demo home；输出是全局 endpoint/direct
    surfaces。调用方是 golden 主测试；副作用是通过 workspace/file endpoint 读取
    fixture 内固定 AGENTS.md，保证 workspace_file 的成功路径也被锁住。
    """
    trace_latest = _get_json(client, "/api/trace/latest")
    trace_id = _first_item_id(trace_latest)
    workspace_docs = _get_json(client, "/api/workspace/docs", {"include_content": "true"})
    workspace_file = demo_home / "AGENTS.md"
    return {
        "api_health": _get_json(client, "/api/health"),
        "api_meta": _get_json(client, "/api/meta"),
        "api_owners": _get_json(client, "/api/owners"),
        "api_agents": _get_json(client, "/api/agents"),
        "api_trace_latest": trace_latest,
        "api_trace_explain_trace": (
            _get_json(client, f"/api/trace/explain/{trace_id}") if trace_id else {"skipped": "no trace id"}
        ),
        "api_workspace_docs": workspace_docs,
        "api_workspace_file": _get_json(client, "/api/workspace/file", {"path": str(workspace_file)}),
        "direct_meta": reader.meta(),
        "direct_owners": reader.owners(),
        "direct_agents_default_selected": reader.agents("agent", OWNER_ID),
        "direct_agents_qing_selected": reader.agents("agent", AGENT_B_ID),
        "direct_trace_latest": reader.trace_latest(limit=20),
        "direct_workspace_roots": reader.workspace_roots(),
        "direct_workspace_docs": reader.workspace_docs(limit=80, include_content=True),
        "direct_workspace_file": reader.workspace_file(str(workspace_file)),
    }


def _id_placeholder(raw: str, id_map: dict[str, str]) -> str:
    """把 opaque id 映射成首次出现序号占位符。

    输入是原始 id 和本次 normalize 运行内的映射表；输出是 `<id:N>` 形式。调用方
    是递归 normalizer；副作用只是在 id_map 中记录第一次出现顺序。相同随机 id
    在同一 payload 内会得到同一占位符，列表不排序，因此返回顺序变化仍会进入 diff。
    """
    if raw not in id_map:
        id_map[raw] = f"<id:{len(id_map) + 1:04d}>"
    return id_map[raw]


def _is_id_key(key: str) -> bool:
    """判断字段名是否承载 id 语义。

    输入是当前 JSON 字段名；输出布尔值。调用方是 normalizer 的 key-aware 分支；
    它只识别 `id` 和 `_id` 后缀，不处理普通业务 key，避免把稳定文本误判成 id。
    """
    return key == "id" or key.endswith("_id")


def _is_timestamp_key(key: str) -> bool:
    """判断字段名是否承载时间戳语义。

    输入是当前 JSON 字段名；输出布尔值。调用方是 normalizer；它覆盖 WebUI reader
    常见 `*_at`、`*_ts`、mtime、modified_at 与 start/end 时间字段。字段值仍需像
    日期时间或 epoch，避免普通文本被误替换。
    """
    return (
        key.endswith("_at")
        or key.endswith("_ts")
        or key in {"at", "iso", "mtime", "modified_at", "start", "end", "starts_at", "ends_at"}
        or "timestamp" in key
    )


def _has_surface(path: tuple[str, ...], surface: str) -> bool:
    """判断递归路径是否位于某个 reader surface 内。

    输入是递归访问路径和 surface 名；输出布尔值。调用方是 live-now normalizer；
    它同时覆盖嵌套 surface（如 `api_snapshot.clock`）和 direct surface（如
    `direct_clock`），但不匹配普通文本值，避免把稳定业务字段误判为 live 字段。
    """
    return any(part == surface or part.endswith(f"_{surface}") for part in path)


def _is_live_now_path(path: tuple[str, ...]) -> bool:
    """识别 reader 输出中由当前时钟派生的字段路径。

    输入是递归访问路径；输出布尔值。调用方是 normalizer；它只中和 clock/meals 和
    snapshot rebuild time 的 live-now 派生值，保留其它 seeded 业务文本，避免
    Date.now 类漂移污染 golden。
    """
    if not path:
        return False
    key = path[-1]
    if _has_surface(path, "clock") and key in {"iso", "hour", "hhmm", "phase", "label"}:
        return True
    if _has_surface(path, "meals_today") and key == "date":
        return True
    if path == ("api_snapshot", "updated_at"):
        return True
    return "countdown" in key or key in {"now", "current_time"}


def _looks_like_epoch(value: int | float) -> bool:
    """判断数字是否落在现代 epoch seconds 区间。

    输入是数字；输出布尔值。调用方是 timestamp normalizer，仅在字段名也带时间语义
    时使用。这个区间覆盖 2000-01-01 到 2100-01-01，避免资源数值被误当时间。
    """
    return 946_684_800 <= float(value) <= 4_102_444_800


def _replace_roots(value: str, replacements: tuple[tuple[str, str], ...]) -> str:
    """把本机绝对路径替换为稳定 root token。

    输入是字符串和 root 替换表；输出是替换后的字符串。调用方是 normalizer 的
    字符串分支；副作用无。这样 temp HERMES_HOME 和本地 repo 路径不会进入 golden。
    """
    out = value
    for raw, token in replacements:
        out = out.replace(raw, token)
    return out


def _normalize(value: Any, id_map: dict[str, str], replacements: tuple[tuple[str, str], ...], path: tuple[str, ...] = ()) -> Any:
    """递归 normalizer：只中和 volatile 值，保留结构、字段和值顺序。

    输入是任意 JSON-like 值、id 映射、路径替换表和当前位置；输出是 normalized 值。
    调用方是 golden capture 写入与比较。副作用仅为 id_map 记录首次出现顺序；函数
    不删除字段、不排序 list、不合并对象，因此字段增删、值漂移和列表重排都会出现在
    后续 diff 中。
    """
    key = path[-1] if path else ""
    if isinstance(value, dict):
        return {str(k): _normalize(v, id_map, replacements, (*path, str(k))) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(item, id_map, replacements, (*path, "[]")) for item in value]
    if isinstance(value, str):
        text = _replace_roots(value, replacements)
        if key.endswith("_json") and text[:1] in {"{", "["}:
            try:
                nested = json.loads(text)
            except json.JSONDecodeError:
                nested = None
            if nested is not None:
                normalized_nested = _normalize(nested, id_map, replacements, (*path, "<json>"))
                return json.dumps(normalized_nested, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if _is_live_now_path(path):
            return "<now>"
        if "hash" in key.lower() or HEX_HASH_RE.fullmatch(text):
            return "<hash>"
        if _is_timestamp_key(key) and (ISO_DATETIME_RE.fullmatch(text) or DATE_RE.fullmatch(text)):
            return "<ts>"
        if ISO_DATETIME_RE.fullmatch(text):
            return "<ts>"
        if text in STABLE_ID_VALUES:
            return text
        if _is_id_key(key) and (OPAQUE_ID_RE.fullmatch(text) or UUID_RE.fullmatch(text) or CANON_ID_RE.fullmatch(text)):
            return _id_placeholder(text, id_map)
        if _is_id_key(key) and key == "id":
            return _id_placeholder(text, id_map)
        if OPAQUE_ID_RE.fullmatch(text) or UUID_RE.fullmatch(text) or CANON_ID_RE.fullmatch(text):
            return _id_placeholder(text, id_map)
        if EMBEDDED_OPAQUE_ID_RE.search(text):
            return EMBEDDED_OPAQUE_ID_RE.sub(lambda match: _id_placeholder(match.group(1), id_map), text)
        return text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if _is_live_now_path(path):
            return "<now>"
        if _is_timestamp_key(key) and _looks_like_epoch(value):
            return "<ts>"
        if _is_id_key(key):
            return _id_placeholder(str(value), id_map)
    return value


def _normalize_capture(capture: dict[str, Any], demo_home: Path) -> dict[str, Any]:
    """对一次完整捕获应用确定性 volatile-field normalizer。

    输入是 raw capture 和 demo home；输出是可提交到 golden 的 normalized capture。
    调用方是主测试；副作用无。id_map 覆盖整个文件，保证同一个文件内跨 endpoint
    引用同一对象时得到同一个 `<id:N>`。
    """
    replacements = (
        (str(demo_home), "<hermes_home>"),
        (str(REPO_ROOT), "<repo_root>"),
    )
    return _normalize(capture, {}, replacements)


def _first_diff(expected: Any, actual: Any, path: str = "$") -> tuple[str, Any, Any] | None:
    """返回 expected 与 actual 的第一处结构化差异。

    输入是 expected、actual 和当前位置；输出为 `(path, expected_value,
    actual_value)` 或 None。调用方是断言失败消息；副作用无。dict 按 golden 键序
    遍历，list 按原始顺序逐项比较，所以不会掩盖 reader 返回顺序变化。
    """
    if type(expected) is not type(actual):
        return (path, expected, actual)
    if isinstance(expected, dict):
        expected_keys = set(expected)
        actual_keys = set(actual)
        missing = expected_keys - actual_keys
        if missing:
            key = sorted(missing)[0]
            return (f"{path}.{key}", expected[key], "<missing>")
        extra = actual_keys - expected_keys
        if extra:
            key = sorted(extra)[0]
            return (f"{path}.{key}", "<missing>", actual[key])
        for key in expected:
            diff = _first_diff(expected[key], actual[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(expected, list):
        for index, (exp_item, act_item) in enumerate(zip(expected, actual)):
            diff = _first_diff(exp_item, act_item, f"{path}[{index}]")
            if diff:
                return diff
        if len(expected) != len(actual):
            return (f"{path}.length", len(expected), len(actual))
        return None
    if expected != actual:
        return (path, expected, actual)
    return None


def _short_json(value: Any) -> str:
    """把 diff 片段格式化成短 JSON 文本。

    输入是任意 JSON-like 值；输出最多 1200 字符的字符串。调用方是 golden 断言失败
    消息；副作用无。截断只影响报错可读性，不参与比较。
    """
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return text if len(text) <= 1200 else text[:1200] + "..."


def _assert_matches_golden(name: str, actual: dict[str, Any]) -> None:
    """比较或重生一个 golden JSON 文件。

    输入是 golden 文件名和 normalized capture；输出为空。调用方是主测试；副作用在
    `HERMES_REGEN_GOLDEN=1` 时写入 tests/golden/webui_reader。默认比较模式下，
    缺文件或内容漂移都会失败，并报告具体 surface 与第一处差异路径。
    """
    path = GOLDEN_DIR / name
    if REGEN_GOLDEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert path.exists(), f"Missing golden file {path}. Regenerate with HERMES_REGEN_GOLDEN=1."
    expected = json.loads(path.read_text(encoding="utf-8"))
    diff = _first_diff(expected, actual)
    assert diff is None, (
        f"{name} drift at {diff[0]}\n"
        f"expected:\n{_short_json(diff[1])}\n"
        f"actual:\n{_short_json(diff[2])}"
    )


def test_lifeengine_webui_reader_golden(demo_env: dict[str, Path]) -> None:
    """锁定当前 WebUI reader 对双 agent demo seed 的 normalized JSON 输出。

    输入是当前测试临时 demo 环境；输出为空。调用方是默认 pytest 快测套件。副作用在
    默认模式下只有只读 DB/API 调用；设置 `HERMES_REGEN_GOLDEN=1` 时会重写 golden
    JSON。失败意味着 reader 的前端 JSON 合同发生了字段、值、shape 或列表顺序漂移。
    """
    reader = LifeEngineReader(str(demo_env["db"]))
    client = TestClient(create_app(str(demo_env["db"])))

    global_capture = _normalize_capture(_capture_global_surfaces(client, reader, demo_env["home"]), demo_env["home"])
    _assert_matches_golden("global.json", global_capture)

    for owner_kind, owner_id in OWNERS:
        raw_owner_capture = _capture_owner_surfaces(client, reader, owner_kind, owner_id)
        normalized_owner_capture = _normalize_capture(raw_owner_capture, demo_env["home"])
        _assert_matches_golden(f"{owner_kind}__{owner_id}.json", normalized_owner_capture)
