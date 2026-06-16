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
import socket
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .reader import LifeEngineReader, resolve_lifeengine_db

_THIS_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _THIS_DIR / "static"


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
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
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
    def __init__(self, life_dir: str | None = None):
        self.db_path = resolve_lifeengine_db(life_dir)
        self.owner_kind = "agent"
        self.owner_id = "default-agent"
        try:
            reader = LifeEngineReader(str(self.db_path))
            owners = reader.owners()
            if owners:
                self.owner_kind = owners[0]["owner_kind"]
                self.owner_id = owners[0]["owner_id"]
        except Exception:
            pass

    def reader(self) -> LifeEngineReader:
        return LifeEngineReader(str(self.db_path))

    def select(self, path: str) -> dict[str, Any]:
        self.db_path = resolve_lifeengine_db(path)
        reader = self.reader()
        owners = reader.owners()
        if owners:
            self.owner_kind = owners[0]["owner_kind"]
            self.owner_id = owners[0]["owner_id"]
        return {"ok": True, "meta": reader.meta(), "owners": owners, "selected_owner": {"owner_kind": self.owner_kind, "owner_id": self.owner_id}}

    def set_owner(self, owner_kind: str, owner_id: str) -> dict[str, Any]:
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        return {"ok": True, "owner": {"owner_kind": owner_kind, "owner_id": owner_id}}


def create_app(life_dir: str | None = None) -> FastAPI:
    state = WebUIState(life_dir)
    app = FastAPI(title="LifeEngine WebUI", version="0.13.0")
    app.state.lifeengine_webui = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost", "http://127.0.0.1:8765", "http://localhost:8765"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
            return {"ok": True, "webui_version": "0.13.0", "meta": meta}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
    def event_detail(event_id: str) -> dict[str, Any]:
        return state.reader().event_detail(event_id)

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

    @app.get("/api/trace/latest")
    def trace_latest(limit: int = 20) -> dict[str, Any]:
        return {"items": state.reader().trace_latest(limit=limit)}

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
                if req.action == "review_apply_all":
                    return rt.review("apply_all", state.owner_kind, state.owner_id, None, None, section=req.payload.get("section"), safe_only=True, limit=int(req.payload.get("limit") or 5))
                if req.action == "sleep_recovery_plan":
                    return rt.sleep("recovery_plan", state.owner_kind, state.owner_id, None, None)
                return {"ok": False, "error": f"Unknown action: {req.action}"}
            finally:
                rt.close()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/asset")
    def serve_asset(path: str = Query(...)):
        """Serve image files from allowed directories (image_cache, assets, webui static).

        Accepts both absolute paths and relative paths. Relative paths are
        resolved against HERMES_HOME subdirectories (image_cache, assets,
        assets/*) so that DB-stored asset_uri values like 'iris-wardrobe/x.png'
        work without the frontend needing to know HERMES_HOME.
        """
        requested = _resolve_asset_path(path)
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
    async def stream(period: str = "today", date: str | None = None):
        async def gen():
            last = None
            while True:
                try:
                    snap = state.reader().snapshot(state.owner_kind, state.owner_id, period=period, date=date)
                    h = snap.get("snapshot_hash")
                    if h != last:
                        yield f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                        last = h
                    else:
                        yield f"event: heartbeat\ndata: {json.dumps({'hash': h, 'at': time.time()})}\n\n"
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
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except Exception:
        print("LifeEngine WebUI requires uvicorn and fastapi. Install: pip install fastapi uvicorn", file=sys.stderr)
        return 2
    if not _port_available(args.host, args.port):
        print(f"Port {args.port} on {args.host} is already in use.", file=sys.stderr)
        return 2
    app = create_app(args.life_dir)
    url = f"http://{args.host}:{args.port}"
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"LifeEngine WebUI running at {url}")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
