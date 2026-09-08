from __future__ import annotations

import os
import threading
import time
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Config, load_config, local_url
from .db import get_document, init_db, search, status
from .indexer import SyncLoop, start_sync, sync_status
from . import ai
from .db import embedding_status


ROOT = Path(__file__).resolve().parent


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=20)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    loop = SyncLoop(config)
    @asynccontextmanager
    async def lifespan(app):
        init_db()
        loop.start()
        try:
            yield
        finally:
            loop.stop()

    app = FastAPI(title="Memory Hub", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def access_boundary(request: Request, call_next):
        host = request.url.hostname
        # Preserve wildcard bindings for existing virtual-network users.
        allowed = {config.host, "localhost", "127.0.0.1", "::1"}
        if config.host not in {"0.0.0.0", "::"} and host not in allowed:
            return JSONResponse({"detail": "Untrusted Host"}, status_code=400)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/health/live" and config.api_token_env:
            expected = os.environ.get(config.api_token_env, "")
            if not expected and os.name == "nt":
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as values:
                        expected = winreg.QueryValueEx(values, config.api_token_env)[0]
                except OSError:
                    pass
            supplied = request.headers.get("authorization", "")
            if not expected or not secrets.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
                return JSONResponse({"detail": "需要有效的访问口令"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    # Share one calculation across tabs/clients; sync state itself stays live.
    stats_lock = threading.Lock()
    stats_snapshot: dict = {}
    stats_expires = 0.0

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/api/health/live")
    def api_health_live() -> dict:
        return {"status": "ok"}

    @app.get("/api/agent-prompt")
    def api_agent_prompt() -> dict:
        endpoint = local_url(config)
        mode = "已开启 full_memory_mode，模型请求可能包含记录原文。" if config.full_memory_mode else "未开启 full_memory_mode，模型出站请求会尽力脱敏明显凭据，但不保证识别所有秘密。"
        auth = f"\nHTTP 请求需使用环境变量 {config.api_token_env} 的值作为 Bearer 口令；不要把口令写进提示词。" if config.api_token_env else ""
        prompt = (f"先查询我的本机 Memory Hub，再回答历史进度。需要 Agent 具备访问本机 HTTP 或 MCP 的能力。\n"
                  f"关键词检索：GET {endpoint}/api/search?q=<URL 编码的关键词>\n"
                  f"归纳问答：POST {endpoint}/api/ask，JSON：{{\"question\":\"问题\"}}；此功能需启用模型，可能产生费用和向外部发送记录。\n"
                  "列出相关来源、已完成内容和下一步；找不到就明确说明。Qoder metadata only 仅是线索。"
                  "不要修改来源记录或调用同步接口。\n" + mode + auth)
        return {"prompt": prompt, "full_memory_mode": config.full_memory_mode}

    @app.get("/embedding-progress")
    def embedding_progress() -> FileResponse:
        return FileResponse(ROOT / "static" / "embedding-progress.html")

    @app.get("/api/search")
    def api_search(q: str = "", source: str | None = None, project: str | None = None, limit: int = Query(30, ge=1, le=100), owner: str | None = None) -> dict:
        return {"results": search(q, source, project, limit, owner)}

    @app.post("/api/sync")
    def api_sync() -> dict:
        return start_sync(config)

    @app.get("/api/status")
    def api_status() -> dict:
        nonlocal stats_snapshot, stats_expires
        model = (config.embedding or {}).get("model")
        with stats_lock:
            if time.monotonic() >= stats_expires:
                stats_snapshot = embedding_status(model)
                stats_expires = time.monotonic() + 10
            snapshot = stats_snapshot
        return {"sources": status(), "host": config.host, "port": config.port, "sync": sync_status(), "embedding": snapshot, "ai_enabled": bool((config.ai or {}).get("enabled"))}

    @app.post("/api/embeddings/sync")
    def api_embedding_sync(limit: int = Query(100, ge=1, le=500)) -> dict:
        try:
            return ai.sync_embeddings(config, limit)
        except ai.AIConfigError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/ask")
    def api_ask(request: AskRequest) -> dict:
        try:
            return ai.ask(config, request.question, request.limit)
        except ai.AIConfigError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/documents/{document_id}")
    def api_document(document_id: str) -> dict:
        document = get_document(document_id)
        if not document:
            raise HTTPException(404, "Document not found")
        return document

    @app.post("/api/documents/{document_id}/open")
    def api_open(document_id: str) -> dict:
        document = get_document(document_id)
        if not document:
            raise HTTPException(404, "Document not found")
        locator = Path(document["locator"])
        if not locator.exists():
            raise HTTPException(404, "Original source is no longer available")
        if os.name == "nt":
            os.startfile(str(locator if locator.is_dir() else locator.parent))
        return {"opened": str(locator)}

    return app


app = create_app()
