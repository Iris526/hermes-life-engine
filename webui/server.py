"""LifeEngine WebUI server.

Run with:
  python -m lifeengine.webui.server --life-dir ~/.hermes/lifeengine
or through Hermes:
  hermes lifeengine webui --open
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .reader import LifeEngineReader, resolve_lifeengine_db

_THIS_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _THIS_DIR / "static"
_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}
_WRITE_PATH_PREFIXES = (
    "/api/action",
    "/api/select",
    "/api/owner",
)


def _test_context_active() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST") or os.getenv("LIFEENGINE_TEST_CONTEXT", "").strip().lower() in _TRUTHY)


def _token_path() -> Path:
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()
    return hermes_home / "lifeengine" / "webui.token"


def resolve_webui_token(*, explicit: str | None = None) -> str:
    """Resolve the operator token used to authorize WebUI write APIs.

    Precedence: explicit arg → ``LIFEENGINE_WEBUI_TOKEN`` → persisted file under
    ``$HERMES_HOME/lifeengine/webui.token`` → generate + persist a new token.
    """
    if explicit:
        return str(explicit).strip()
    env = (os.getenv("LIFEENGINE_WEBUI_TOKEN") or "").strip()
    if env:
        return env
    path = _token_path()
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except Exception:
        pass
    token = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return token


def _client_host(request: Request) -> str:
    try:
        client = request.client
        return (client.host if client else "") or ""
    except Exception:
        return ""


def _is_loopback_client(request: Request) -> bool:
    host = _client_host(request).strip().lower().rstrip(".")
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        ip = __import__("ipaddress").ip_address(host.strip("[]"))
        return bool(ip.is_loopback)
    except Exception:
        return False


def _extract_request_token(request: Request) -> str:
    header = (request.headers.get("x-lifeengine-token") or "").strip()
    if header:
        return header
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.query_params.get("token") or "").strip()


def _is_write_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _WRITE_PATH_PREFIXES)


def _allowed_asset_roots() -> list[Path]:
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    allowed_roots = [
        hermes_home / "image_cache",
        hermes_home / "assets",
        hermes_home / "assets" / "collections",
        _STATIC_DIR / "assets",
    ]
    assets_dir = hermes_home / "assets"
    if assets_dir.is_dir():
        for child in assets_dir.iterdir():
            if child.is_dir():
                allowed_roots.append(child)
    return [root.resolve() for root in allowed_roots]


def _resolve_asset_path(path: str) -> Path:
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    allowed_roots = _allowed_asset_roots()
    raw = Path(path)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw.expanduser().resolve())
    else:
        rel = str(raw).lstrip("./")
        for root in allowed_roots:
            candidates.append((root / rel).resolve())
        candidates.append((hermes_home / rel).resolve())
    for candidate in candidates:
        if any(candidate == root or root in candidate.parents for root in allowed_roots) and candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="Asset not found or outside allowed directories")


def _asset_media_type(path: Path) -> str:
    # SVG is intentionally NOT image/svg+xml — browsers would execute scripts in
    # the WebUI origin. Serve as octet-stream / force download when allowed at all.
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp",
                   ".svg": "application/octet-stream"}
    return media_types.get(path.suffix.lower(), "application/octet-stream")


def _thumbnail_path(source: Path, max_width: int, max_height: int) -> Path:
    stat = source.stat()
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    cache_dir = hermes_home / "cache" / "lifeengine-webui" / "thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{max_width}x{max_height}".encode()).hexdigest()
    return cache_dir / f"{key}.jpg"


def _generate_thumbnail(source: Path, max_width: int, max_height: int) -> Path:
    from PIL import Image, ImageOps

    target = _thumbnail_path(source, max_width, max_height)
    if target.is_file():
        return target
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, (10, 6, 18))
            background.paste(image, mask=image.convert("RGBA").getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.save(target, format="JPEG", quality=78, optimize=True, progressive=True)
    return target


class SelectRequest(BaseModel):
    path: str


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


class WebUIState:
    def __init__(self, life_dir: str | None = None, *, auth_token: str | None = None):
        self.db_path = resolve_lifeengine_db(life_dir)
        self.owner_kind = "agent"
        self.owner_id = "default-agent"
        self.auth_token = resolve_webui_token(explicit=auth_token)
        try:
            reader = LifeEngineReader(str(self.db_path))
            owners = reader.owners()
            owner = self._preferred_owner(owners)
            if owner:
                self.owner_kind = owner["owner_kind"]
                self.owner_id = owner["owner_id"]
        except Exception:
            pass

    def _preferred_owner(self, owners: list[dict[str, str]]) -> dict[str, str] | None:
        """选择 WebUI 初始观察 owner。

        输入是 reader.owners() 的候选列表；输出优先 `agent:default-agent`，否则为
        第一位 owner。调用方是初始化和选择 DB 后的重定位；副作用无。这样单 agent
        仍保持旧行为，多 agent demo 则稳定先落在 A/凛，再由 switcher 切到 B。
        """
        for owner in owners:
            if owner.get("owner_kind") == "agent" and owner.get("owner_id") == "default-agent":
                return owner
        return owners[0] if owners else None

    def reader(self) -> LifeEngineReader:
        return LifeEngineReader(str(self.db_path))

    def select(self, path: str) -> dict[str, Any]:
        self.db_path = resolve_lifeengine_db(path)
        reader = self.reader()
        owners = reader.owners()
        owner = self._preferred_owner(owners)
        if owner:
            self.owner_kind = owner["owner_kind"]
            self.owner_id = owner["owner_id"]
        return {"ok": True, "meta": reader.meta(), "owners": owners, "selected_owner": {"owner_kind": self.owner_kind, "owner_id": self.owner_id}}

    def set_owner(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        return {"ok": True, "owner": {"owner_kind": owner_kind, "owner_id": owner_id}}


class _WriteAuthMiddleware(BaseHTTPMiddleware):
    """Require the operator token on all mutating API routes.

    Loopback browsers bootstrap the token via ``GET /api/auth/session``. Pytest
    bypasses the gate so existing TestClient suites keep working without headers.
    """

    def __init__(self, app, state: WebUIState):
        super().__init__(app)
        self._state = state

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or ""
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and _is_write_path(path):
            if not _test_context_active():
                provided = _extract_request_token(request)
                if not provided or not secrets.compare_digest(provided, self._state.auth_token):
                    return JSONResponse(
                        status_code=401,
                        content={
                            "ok": False,
                            "error": "unauthorized",
                            "message": "写接口需要 X-LifeEngine-Token。本机打开观星台后会自动从 /api/auth/session 领取。",
                        },
                    )
        return await call_next(request)


def create_app(life_dir: str | None = None, *, auth_token: str | None = None) -> FastAPI:
    state = WebUIState(life_dir, auth_token=auth_token)
    app = FastAPI(title="LifeEngine WebUI", version="0.18.0")
    app.state.lifeengine_webui = state
    app.add_middleware(_WriteAuthMiddleware, state=state)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost", "http://127.0.0.1:8765", "http://localhost:8765"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-LifeEngine-Token", "Authorization"],
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            reader = state.reader()
            meta = reader.meta()
            return {"ok": True, "webui_version": "0.18.0", "meta": meta, "auth_required_for_writes": True}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/auth/session")
    def auth_session(request: Request) -> dict[str, Any]:
        """Bootstrap the operator token for loopback browsers only.

        Non-loopback clients never receive the token here — they must already
        hold ``LIFEENGINE_WEBUI_TOKEN`` / a manually issued secret.
        """
        if not (_is_loopback_client(request) or _test_context_active()):
            raise HTTPException(status_code=403, detail="auth bootstrap is loopback-only")
        return {
            "ok": True,
            "token": state.auth_token,
            "header": "X-LifeEngine-Token",
            "local": True,
        }

    @app.post("/api/select")
    def select(req: SelectRequest) -> dict[str, Any]:
        try:
            return state.select(req.path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return state.reader().meta()

    @app.get("/api/owners")
    def owners() -> dict[str, Any]:
        return {"owners": state.reader().owners(), "selected": {"owner_kind": state.owner_kind, "owner_id": state.owner_id}}

    @app.get("/api/agents")
    def agents() -> list[dict[str, Any]]:
        """返回顶栏 constellation roster。

        输入来自当前 WebUIState 的 selected owner；输出为 active agent 数组，每项含
        owner_kind、owner_id、name、engine_state、is_selected。调用方是静态 WebUI
        的 agent switcher；副作用只读数据库，不改变既有 /api/owners shape。
        """
        return state.reader().agents(state.owner_kind, state.owner_id)

    @app.post("/api/owner")
    async def owner(request: Request) -> dict[str, Any]:
        body = await request.json()
        return state.set_owner(body.get("owner_kind") or "agent", body.get("owner_id") or "default-agent")

    @app.get("/api/snapshot")
    def snapshot(period: str = Query("today"), date: str | None = None, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        ok = owner_kind or state.owner_kind
        oid = owner_id or state.owner_id
        return state.reader().snapshot(ok, oid, period=period, date=date)

    @app.get("/api/state")
    def realtime(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return state.reader().realtime_state(owner_kind or state.owner_kind, owner_id or state.owner_id)

    @app.get("/api/schedule")
    def schedule(period: str = Query("today"), date: str | None = None, include_completed: bool = True, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return state.reader().schedule(owner_kind or state.owner_kind, owner_id or state.owner_id, period=period, date=date, include_completed=include_completed)

    @app.get("/api/events")
    def events(status: str | None = None, limit: int = 100, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().events(owner_kind or state.owner_kind, owner_id or state.owner_id, status=status, limit=limit)}


    @app.get("/api/event/{event_id}")
    def event_detail(event_id: str, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return state.reader().event_detail(
            event_id,
            owner_kind=owner_kind or state.owner_kind,
            owner_id=owner_id or state.owner_id,
        )

    @app.get("/api/dream/{dream_id}")
    def dream_detail(dream_id: str) -> dict[str, Any]:
        return state.reader().dream_detail(dream_id)

    @app.get("/api/trace/explain/{object_id}")
    def trace_explain(object_id: str) -> dict[str, Any]:
        return state.reader().trace_explain(object_id)

    @app.get("/api/review")
    def review(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().review_items(owner_kind or state.owner_kind, owner_id or state.owner_id)}

    @app.get("/api/resources")
    def resources(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().resources(owner_kind or state.owner_kind, owner_id or state.owner_id)}

    @app.get("/api/dreams")
    def dreams(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().dreams(owner_kind or state.owner_kind, owner_id or state.owner_id)}

    @app.get("/api/campaigns")
    def campaigns(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().campaigns(owner_kind or state.owner_kind, owner_id or state.owner_id)}

    @app.get("/api/goals")
    def goals(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        """读取当前 owner 的目标 read model，供 Observatory 的目标面板消费。"""
        return state.reader().goals(owner_kind or state.owner_kind, owner_id or state.owner_id)

    @app.get("/api/rhythm")
    def rhythm(date: str | None = None, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        """读取当前 owner 的每日节律 read model，未传 date 时使用最新可用日期。"""
        return state.reader().daily_rhythm(owner_kind or state.owner_kind, owner_id or state.owner_id, date=date)

    @app.get("/api/feed")
    def feed(owner_kind: str | None = None, owner_id: str | None = None,
             before: str | None = None, limit: int = 40) -> dict[str, Any]:
        return state.reader().life_feed(owner_kind or state.owner_kind, owner_id or state.owner_id,
                                        before=before, limit=max(1, min(int(limit), 100)))

    @app.get("/api/changefeed")
    def changefeed(since: int = 0, limit: int = 200,
                   owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        """读取 life_journal rowid 增量，供前端做 targeted 动效。

        输入来自查询参数：since 是上一 cursor，limit 是本次最多事件数；limit=0
        只 prime 当前最大 cursor，不返回历史事件。owner 参数缺省时沿用 WebUI
        当前 selected owner。输出保持 `{cursor, events}`，不改变 `/api/snapshot`
        合同。副作用只读 SQLite；旧库缺 journal 时 reader 会降级为空增量。
        """
        return state.reader().journal_changefeed(
            owner_kind or state.owner_kind,
            owner_id or state.owner_id,
            since_rowid=max(0, int(since or 0)),
            limit=max(0, min(int(limit if limit is not None else 200), 10000)),
        )

    @app.get("/api/inner_life")
    def inner_life(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return state.reader().inner_life(owner_kind or state.owner_kind, owner_id or state.owner_id)

    @app.get("/api/relationship")
    def relationship(owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {"items": state.reader().relationship_notes(owner_kind or state.owner_kind, owner_id or state.owner_id)}

    @app.get("/api/social_world")
    def social_world(owner_kind: str | None = None, owner_id: str | None = None, limit: int = 80) -> dict[str, Any]:
        return state.reader().social_world(owner_kind or state.owner_kind, owner_id or state.owner_id, limit=limit)

    @app.get("/api/world_model")
    def world_model(owner_kind: str | None = None, owner_id: str | None = None, limit: int = 80) -> dict[str, Any]:
        return state.reader().world_model(owner_kind or state.owner_kind, owner_id or state.owner_id, limit=limit)

    @app.get("/api/trace/latest")
    def trace_latest(limit: int = 20, owner_kind: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        return {
            "items": state.reader().trace_latest(
                limit=limit,
                owner_kind=owner_kind or state.owner_kind,
                owner_id=owner_id or state.owner_id,
            )
        }

    @app.get("/api/workspace/docs")
    def workspace_docs(include_content: bool = False, limit: int = 80) -> dict[str, Any]:
        return state.reader().workspace_docs(limit=limit, include_content=include_content)

    @app.get("/api/workspace/file")
    def workspace_file(path: str = Query(...)) -> dict[str, Any]:
        try:
            return state.reader().workspace_file(path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/action")
    def action(req: ActionRequest) -> dict[str, Any]:
        # Operator bridge: only for the current Hermes profile DB. Arbitrary selected
        # dirs are intentionally read-only to avoid silently mutating the wrong agent.
        try:
            from ..paths import db_path as active_db_path
            if Path(active_db_path()).resolve() != state.db_path.resolve():
                return {"ok": False, "mode": "read_only", "message": "当前选择的目录不是运行中 Hermes profile 的 LifeEngine DB；WebUI 只读。"}
            from ..runtime import LifeEngineRuntime
            rt = LifeEngineRuntime()
            try:
                if req.action == "tick":
                    return rt.tick(state.owner_kind, state.owner_id, manual=True)
                if req.action == "start":
                    resume = rt.control("resume", state.owner_kind, state.owner_id, reason="webui manual start")
                    heartbeat = rt.control("heartbeat", state.owner_kind, state.owner_id, mode="hermes_cron")
                    tick = rt.tick(state.owner_kind, state.owner_id, manual=True)
                    return {"ok": resume.get("ok", True) and heartbeat.get("ok", True), "resume": resume, "heartbeat": heartbeat, "tick": tick}
                if req.action == "call":
                    return rt.call(state.owner_kind, state.owner_id, reason="webui call", message_text=req.payload.get("message_text"), user_id=req.payload.get("user_id"))
                if req.action == "review_apply":
                    return rt.review("apply", state.owner_kind, state.owner_id, None, None, item_id=req.payload.get("item_id"), choice=req.payload.get("choice"))
                if req.action == "review_dismiss":
                    # Dismiss marks the item resolved WITHOUT executing its action.
                    # Previously the UI's "忽略" button routed through review_apply,
                    # which for safe-auto items ran the action — the opposite of the
                    # user's intent. Route it to the real dismiss path instead.
                    return rt.review("dismiss", state.owner_kind, state.owner_id, None, None, item_id=req.payload.get("item_id"), reason="dismissed via observatory")
                if req.action == "review_apply_all":
                    return rt.review("apply_all", state.owner_kind, state.owner_id, None, None, section=req.payload.get("section"), safe_only=True, limit=int(req.payload.get("limit") or 5))
                if req.action == "world":
                    world_action = req.payload.get("world_action") or req.payload.get("action")
                    payload = {k: v for k, v in req.payload.items() if k not in {"world_action", "action"}}
                    return rt.world(world_action, state.owner_kind, state.owner_id, None, None, **payload)
                if req.action == "social":
                    social_action = req.payload.get("social_action") or req.payload.get("action")
                    payload = {k: v for k, v in req.payload.items() if k not in {"social_action", "action"}}
                    return rt.social(social_action, state.owner_kind, state.owner_id, None, None, **payload)
                if req.action == "sleep_recovery_plan":
                    return rt.sleep("recovery_plan", state.owner_kind, state.owner_id, None, None)
                if req.action == "proactive_dismiss":
                    return rt.proactive("suppress", state.owner_kind, state.owner_id, None, None,
                                        intent_id=req.payload.get("intent_id"), reason="dismissed from observatory")
                if req.action == "proactive_send":
                    return rt.proactive("send", state.owner_kind, state.owner_id, None, None,
                                        outbox_id=req.payload.get("outbox_id"), manual=True)
                if req.action == "rename":
                    return rt.rename(req.payload.get("name") or "", state.owner_kind, state.owner_id)
                return {"ok": False, "error": f"Unknown action: {req.action}"}
            finally:
                rt.close()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/avatar/{name}")
    def serve_avatar(name: str):
        """Resolve a pluggable avatar/skin asset.

        LifeEngine does not depend on any specific character — the bundled
        sprites/portrait are just the DEFAULT skin. A host can reskin its agent
        by dropping replacement files (same names) into
        ``$HERMES_HOME/lifeengine/avatar/`` with no code change; we serve the
        host override if present, else fall back to the bundled default.
        """
        safe = os.path.basename(name or "")
        if not safe or "/" in name or "\\" in name or Path(safe).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise HTTPException(status_code=404, detail="bad avatar asset")
        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
        override = (hermes_home / "lifeengine" / "avatar" / safe).resolve()
        avatar_root = (hermes_home / "lifeengine" / "avatar").resolve()
        if avatar_root in override.parents and override.is_file():
            return FileResponse(str(override), media_type=_asset_media_type(override))
        bundled = (_STATIC_DIR / "assets" / safe).resolve()
        if (_STATIC_DIR / "assets").resolve() in bundled.parents and bundled.is_file():
            return FileResponse(str(bundled), media_type=_asset_media_type(bundled))
        raise HTTPException(status_code=404, detail="avatar asset not found")

    @app.get("/api/asset")
    def serve_asset(path: str = Query(...)):
        """Serve image files from allowed directories (image_cache, assets, webui static).

        Accepts both absolute paths and relative paths. Relative paths are
        resolved against HERMES_HOME subdirectories (image_cache, assets,
        assets/*) so that DB-stored asset_uri values like 'iris-wardrobe/x.png'
        work without the frontend needing to know HERMES_HOME.
        """
        requested = _resolve_asset_path(path)
        if requested.suffix.lower() == ".svg":
            # Never inline SVG as image/svg+xml in the WebUI origin (scriptable).
            return FileResponse(
                str(requested),
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{requested.name}"'},
            )
        media_type = _asset_media_type(requested)
        return FileResponse(str(requested), media_type=media_type)

    @app.get("/api/asset/preview")
    def serve_asset_preview(path: str = Query(...), max_width: int = 360, max_height: int = 480):
        """Serve a generated preview image while preserving the original asset file."""
        requested = _resolve_asset_path(path)
        if requested.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return FileResponse(str(requested), media_type=_asset_media_type(requested))
        width = max(64, min(int(max_width or 360), 1200))
        height = max(64, min(int(max_height or 480), 1600))
        try:
            preview = _generate_thumbnail(requested, width, height)
        except Exception:
            return FileResponse(str(requested), media_type=_asset_media_type(requested))
        return FileResponse(str(preview), media_type="image/jpeg")

    @app.get("/api/stream")
    async def stream(request: Request, period: str = "today", date: str | None = None):
        async def gen():
            last = None
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ok = state.owner_kind
                    oid = state.owner_id
                    snap = state.reader().snapshot(ok, oid, period=period, date=date)
                    h = snap.get("snapshot_hash")
                    if h != last:
                        yield f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                        last = h
                    else:
                        yield f"event: heartbeat\ndata: {json.dumps({'hash': h, 'at': time.time()})}\n\n"
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(2.0)
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) != 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LifeEngine WebUI / Observatory")
    parser.add_argument("--life-dir", default=None, help="LifeEngine directory or lifeengine.db path. Defaults to $HERMES_HOME/lifeengine.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open browser after startup")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding to a non-loopback host. Requires LIFEENGINE_WEBUI_TOKEN (or uses the persisted local token).",
    )
    parser.add_argument("--token", default=None, help="Operator token for write APIs (default: env or persisted webui.token).")
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except Exception:
        print("LifeEngine WebUI requires uvicorn and fastapi. Install: pip install fastapi uvicorn", file=sys.stderr)
        return 2
    host = str(args.host or "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
        print(
            f"Refusing to bind non-loopback host {host!r} without --allow-remote "
            "(write APIs would be reachable on the network).",
            file=sys.stderr,
        )
        return 2
    if not _port_available(args.host, args.port):
        print(f"Port {args.port} on {args.host} is already in use.", file=sys.stderr)
        return 2
    app = create_app(args.life_dir, auth_token=args.token)
    token = app.state.lifeengine_webui.auth_token
    url = f"http://{args.host}:{args.port}"
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"LifeEngine WebUI running at {url}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("Remote bind enabled: write APIs require X-LifeEngine-Token.")
    else:
        print("Loopback mode: browser will bootstrap the write token via /api/auth/session.")
    # Never print the full token to stdout in remote mode logs if user set env;
    # for local use, show a short fingerprint so operators know a token exists.
    print(f"Write-token fingerprint: {hashlib.sha256(token.encode()).hexdigest()[:12]}…")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
