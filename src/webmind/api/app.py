"""FastAPI application (REST + SSE streaming)."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from importlib import resources
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from webmind.core.config import WebMindConfig
from webmind.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    QueryValidationError,
)
from webmind.core.types import ChatResponse, ExportRef, Principal, RetrievedChunk, TableResult
from webmind.intelligence.analytics import AggregateQuery, RecordQuery
from webmind.runtime import WebMindRuntime
from webmind.storage.models import AuditEvent, Connector
from webmind.version import __version__

log = logging.getLogger("webmind.api")


# ---------------------------------------------------------------- schemas


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = Field(default=None, max_length=64)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=50)


class SearchResponse(BaseModel):
    results: list[RetrievedChunk]


class AnalyticsRequest(BaseModel):
    kind: Literal["records", "aggregate"]
    query: dict[str, Any]


class AnalyticsResponse(BaseModel):
    table: TableResult


class ExportRequest(BaseModel):
    query: RecordQuery
    format: Literal["csv", "json", "xlsx", "pdf"] = "csv"
    title: str | None = Field(default=None, max_length=200)


class AdminSyncRequest(BaseModel):
    source_id: str | None = None
    rebuild: bool = False


class WebhookRequest(BaseModel):
    uris: list[str] | None = Field(default=None, max_length=1000)


# --------------------------------------------------------------- rate limit


class RateLimiter:
    """In-process sliding-window limiter (per user, or per client IP for anonymous)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": "60"})
        q.append(now)
        if len(self._hits) > 50_000:  # bound memory
            for k in list(self._hits)[:10_000]:
                if not self._hits[k]:
                    del self._hits[k]


# --------------------------------------------------------------------- app


def create_app(config: WebMindConfig | None = None, *, runtime: WebMindRuntime | None = None) -> FastAPI:
    if runtime is None:
        if config is None:
            raise ConfigurationError("create_app needs a config or a runtime")
        runtime = WebMindRuntime(config)
    rt = runtime
    cfg = rt.cfg
    limiter = RateLimiter(cfg.server.rate_limit_per_minute)
    background: set[asyncio.Task] = set()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await rt.initialize()
        scheduler = None
        if cfg.synchronization.enabled and cfg.synchronization.scheduler:
            scheduler = asyncio.create_task(_scheduler(rt))
        try:
            yield
        finally:
            if scheduler:
                scheduler.cancel()
            for t in list(background):
                t.cancel()
            await rt.close()

    app = FastAPI(
        title="WebMind API",
        version=__version__,
        description="Self-hosted, permission-aware website intelligence. See /docs for the OpenAPI reference.",
        lifespan=lifespan,
    )
    app.state.runtime = rt
    if cfg.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.server.cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # -- error mapping ----------------------------------------------------------
    @app.exception_handler(AuthenticationError)
    async def _authn(_r: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(AuthorizationError)
    async def _authz(_r: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    @app.exception_handler(NotFoundError)
    async def _nf(_r: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(QueryValidationError)
    async def _qv(_r: Request, exc: QueryValidationError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # -- dependencies -----------------------------------------------------------
    def principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
        token = None
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not value:
                raise AuthenticationError("expected 'Authorization: Bearer <token>'")
            token = value.strip()
        p = rt.identity.verify(token)
        key = p.user_key if p.authenticated else f"ip:{request.client.host if request.client else 'unknown'}"
        limiter.check(f"{p.tenant_id}:{key}")
        return p

    def admin(x_webmind_admin_key: Annotated[str | None, Header()] = None) -> None:
        expected = cfg.server.admin_api_key
        if expected is None:
            raise HTTPException(status_code=403, detail="admin API disabled (server.admin_api_key not set)")
        if not x_webmind_admin_key or not hmac.compare_digest(
            x_webmind_admin_key.encode(), expected.get_secret_value().encode()
        ):
            rt.audit.record(None, "admin_auth", decision="deny", reason="bad admin key", tenant_id="system")
            raise HTTPException(status_code=401, detail="invalid admin key")

    P = Annotated[Principal, Depends(principal)]

    def _spawn(coro) -> None:  # noqa: ANN001
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    # -- health -------------------------------------------------------------------
    @app.get("/v1/health", tags=["runtime"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/health/ready", tags=["runtime"])
    async def ready() -> JSONResponse:
        ok = True
        checks: dict[str, Any] = {}
        try:
            with rt.db.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            ok = False
            checks["database"] = f"error: {type(exc).__name__}"
        checks["keyword_index"] = "ok" if rt.db.check_fts() else "unavailable"
        checks["needs_reindex"] = rt.needs_reindex
        checks["llm"] = rt.llm.info.provider if rt.llm else "none (extractive answers)"
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    # -- chat -----------------------------------------------------------------------
    @app.post("/v1/chat", response_model=ChatResponse, tags=["assistant"])
    async def chat(body: ChatRequest, p: P) -> ChatResponse:
        try:
            return await rt.orchestrator.chat(p, body.message, body.conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/chat/stream", tags=["assistant"])
    async def chat_stream(body: ChatRequest, p: P) -> StreamingResponse:
        rt.policy.require_capability(p, "chat")
        if len(body.message) > cfg.server.max_query_chars:
            raise HTTPException(status_code=400, detail="message too long")

        async def events() -> AsyncIterator[bytes]:
            try:
                async for ev in rt.orchestrator.chat_stream(p, body.message, body.conversation_id):
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n".encode()
            except Exception:
                log.exception("stream failed")
                yield b'event: error\ndata: {"type": "error", "detail": "internal error"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    # -- retrieval & analytics --------------------------------------------------------
    @app.post("/v1/search", response_model=SearchResponse, tags=["retrieval"])
    async def search(body: SearchRequest, p: P) -> SearchResponse:
        rt.policy.require_capability(p, "search")
        rt.policy.require_capability(p, "documents:read")
        return SearchResponse(results=await rt.retriever.search(p, body.query, k=body.top_k))

    @app.post("/v1/analytics/query", response_model=AnalyticsResponse, tags=["analytics"])
    async def analytics(body: AnalyticsRequest, p: P) -> AnalyticsResponse:
        try:
            if body.kind == "records":
                q = RecordQuery.model_validate(body.query)
                table = await rt.datasets.query_records(p, q)
            else:
                aq = AggregateQuery.model_validate(body.query)
                table, _ = await rt.datasets.aggregate(p, aq)
        except AuthorizationError as exc:
            rt.audit.record(
                p, f"api:analytics:{body.kind}", decision="deny", reason=str(exc), details={"query": body.query}
            )
            raise
        except ValueError as exc:  # pydantic validation
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rt.audit.record(
            p,
            f"api:analytics:{body.kind}",
            decision="allow",
            target=table.dataset,
            details={"query": body.query, "rows": len(table.rows)},
        )
        return AnalyticsResponse(table=table)

    @app.post("/v1/exports", response_model=ExportRef, tags=["exports"])
    async def create_export(body: ExportRequest, p: P) -> ExportRef:
        rt.policy.require_capability(p, "export")
        try:
            table = await rt.datasets.query_records(p, body.query, capability="export")
        except AuthorizationError as exc:
            rt.audit.record(p, "api:export", decision="deny", reason=str(exc))
            raise
        ref = await asyncio.to_thread(
            rt.exports.create, p, table, body.format, title=body.title or f"{body.query.dataset} export"
        )
        rt.audit.record(
            p,
            "api:export",
            decision="allow",
            target=table.dataset,
            details={"format": body.format, "rows": len(table.rows)},
        )
        return ref

    @app.get("/v1/exports/{export_id}/download", tags=["exports"])
    async def download_export(export_id: str, p: P) -> Response:
        filename, media, data = await asyncio.to_thread(rt.exports.open, p, export_id)
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/v1/resources", tags=["retrieval"])
    async def resources_list(p: P, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        rt.policy.require_capability(p, "documents:read")
        limit = max(1, min(limit, 1000))
        items = await asyncio.to_thread(rt.list_resources, p, limit, max(0, offset))
        return {"items": items}

    @app.get("/v1/sync/status", tags=["sync"])
    async def sync_status(p: P) -> dict[str, Any]:
        rt.policy.require_capability(p, "sync:status")
        return await asyncio.to_thread(rt.sync_status, p.tenant_id)

    # -- admin ------------------------------------------------------------------------
    @app.post("/v1/admin/sync", status_code=202, tags=["admin"], dependencies=[Depends(admin)])
    async def admin_sync(body: AdminSyncRequest, _bg: BackgroundTasks) -> dict[str, Any]:
        if body.source_id:
            try:
                cfg.source(body.source_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="unknown source") from exc
        rt.audit.record(None, "admin:sync", decision="allow", target=body.source_id or "*", tenant_id="system")
        _spawn(rt.sync(body.source_id, trigger="admin", rebuild=body.rebuild))
        return {"accepted": True, "source_id": body.source_id, "rebuild": body.rebuild}

    @app.post("/v1/admin/reembed", status_code=202, tags=["admin"], dependencies=[Depends(admin)])
    async def admin_reembed() -> dict[str, Any]:
        _spawn(rt.reembed())
        return {"accepted": True}

    @app.get("/v1/admin/audit", tags=["admin"], dependencies=[Depends(admin)])
    async def admin_audit(limit: int = 100, decision: str | None = None) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        with rt.db.session() as s:
            q = select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(limit)
            if decision:
                q = q.where(AuditEvent.decision == decision)
            rows = s.execute(q).scalars().all()
            return {
                "events": [
                    {
                        "ts": e.ts.isoformat(),
                        "tenant": e.tenant_id,
                        "user": e.user_key,
                        "action": e.action,
                        "target": e.target,
                        "decision": e.decision,
                        "reason": e.reason,
                        "details": e.details,
                    }
                    for e in rows
                ]
            }

    @app.get("/v1/admin/sync/status", tags=["admin"], dependencies=[Depends(admin)])
    async def admin_sync_status(tenant: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(rt.sync_status, tenant or cfg.app.default_tenant)

    @app.post("/v1/webhooks/{source_id}", status_code=202, tags=["sync"])
    async def webhook(
        source_id: str, request: Request, x_webmind_signature: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        """Event-driven sync. Body is signed with the source's ``webhook_secret``:
        ``X-WebMind-Signature: sha256=<hex HMAC-SHA256 of the raw body>``."""
        try:
            src = cfg.source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        if src.webhook_secret is None:
            raise HTTPException(status_code=404, detail="not found")
        raw = await request.body()
        if len(raw) > 1_000_000:
            raise HTTPException(status_code=413, detail="payload too large")
        expected = "sha256=" + hmac.new(src.webhook_secret.get_secret_value().encode(), raw, hashlib.sha256).hexdigest()
        if not x_webmind_signature or not hmac.compare_digest(expected, x_webmind_signature):
            rt.audit.record(
                None, "webhook", decision="deny", target=source_id, reason="bad signature", tenant_id=cfg.tenant_of(src)
            )
            raise HTTPException(status_code=401, detail="invalid signature")
        try:
            body = WebhookRequest.model_validate_json(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid body") from exc
        rt.audit.record(
            None,
            "webhook",
            decision="allow",
            target=source_id,
            tenant_id=cfg.tenant_of(src),
            details={"uris": (body.uris or [])[:20]},
        )
        _spawn(rt.sync(source_id, trigger="webhook", uris=body.uris))
        return {"accepted": True}

    # -- embeddable widget ------------------------------------------------------------
    if cfg.server.serve_widget:

        @app.get("/v1/widget.js", tags=["integration"], include_in_schema=False)
        async def widget() -> Response:
            js = resources.files("webmind.api").joinpath("static/webmind-widget.js").read_bytes()
            return Response(
                js,
                media_type="application/javascript",
                headers={"Cache-Control": "public, max-age=300", "Cross-Origin-Resource-Policy": "cross-origin"},
            )

    return app


async def _scheduler(rt: WebMindRuntime) -> None:
    """Run periodic syncs for sources that declare ``sync.interval_minutes``."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            for src in rt.cfg.sources:
                every = src.sync.interval_minutes
                if not every:
                    continue
                with rt.db.session() as s:
                    row = s.get(Connector, src.id)
                    last = row.last_sync_at if row else None
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last is None or (now - last).total_seconds() >= every * 60:
                    await rt.sync(src.id, trigger="schedule")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduled sync failed")
        await asyncio.sleep(30)
