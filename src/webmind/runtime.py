"""WebMind runtime: builds and owns every component from a validated configuration.

The same runtime backs the HTTP API, the CLI and the in-process Python SDK, so Docker
and native deployments execute identical code paths.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from webmind.core.config import WebMindConfig
from webmind.core.errors import ConfigurationError, NotFoundError
from webmind.core.types import Freshness, Principal, RetrievedChunk
from webmind.ingestion.sync import SyncEngine, SyncReport
from webmind.intelligence.analytics import DatasetRegistry
from webmind.intelligence.exports import ExportService
from webmind.intelligence.orchestrator import Orchestrator, create_orchestrator
from webmind.intelligence.retrieval import Retriever, deep_link
from webmind.intelligence.tools import ToolRegistry, default_registry
from webmind.providers.base import EmbeddingProvider, LLMProvider, Reranker
from webmind.providers.embeddings import create_embedder, create_reranker
from webmind.providers.llm import create_llm
from webmind.security.audit import AuditLogger
from webmind.security.identity import IdentityVerifier
from webmind.security.policy import PolicyEngine
from webmind.storage.database import Database
from webmind.storage.models import Chunk, Resource, SyncJob, Tenant
from webmind.storage.object_store import ContentStore, LocalObjectStore
from webmind.storage.search import DatabaseVectorStore, PgVectorStore, VectorStore, keyword_index_for

log = logging.getLogger("webmind")


class WebMindRuntime:
    def __init__(
        self,
        cfg: WebMindConfig,
        *,
        llm: LLMProvider | None | bool = True,
        embedder: EmbeddingProvider | None = None,
        reranker: Reranker | None | bool = True,
    ) -> None:
        """Build components. ``llm``/``embedder``/``reranker`` may be injected (tests, embedding
        apps); by default they are created from configuration."""
        self.cfg = cfg
        self.db = Database(cfg.database.url, base_dir=cfg.base_dir, echo=cfg.database.echo)
        self.store = LocalObjectStore(cfg.resolve_path(cfg.object_store.path))
        self.cas = ContentStore(self.store)
        self.policy = PolicyEngine(cfg.security)
        self.identity = IdentityVerifier(
            cfg.security.authentication, self.policy, default_tenant=cfg.app.default_tenant, base_dir=cfg.base_dir
        )
        self.audit = AuditLogger(self.db)
        self.embedder: EmbeddingProvider = embedder or create_embedder(cfg.embeddings)
        self.llm: LLMProvider | None = create_llm(cfg.llm) if llm is True else (llm or None)
        self.reranker: Reranker | None = (
            create_reranker(cfg.retrieval.reranking) if reranker is True else (reranker or None)
        )
        self.vectors: VectorStore = (
            PgVectorStore(self.db) if cfg.vector_store.provider == "pgvector" else DatabaseVectorStore(self.db)
        )
        self.keyword = keyword_index_for(self.db)
        self.retriever = Retriever(cfg, self.db, self.policy, self.embedder, self.vectors, self.keyword, self.reranker)
        self.sync_engine = SyncEngine(cfg, self.db, self.cas, self.embedder, self.vectors)
        self.datasets = DatasetRegistry(cfg, self.policy)
        self.exports = ExportService(cfg, self.db, self.store)
        self.tools: ToolRegistry = default_registry()
        self.orchestrator: Orchestrator = create_orchestrator(self)
        self.needs_reindex = False
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.db.create_schema()
        try:
            dims = self.embedder.dimensions
        except Exception:
            dims = len(await self.embedder.embed_query("dimension probe"))
        self.vectors.initialize(dims)
        stored = self.db.get_setting("embedding_model")
        if stored is None:
            self.db.set_setting("embedding_model", self.embedder.model_id)
        elif stored != self.embedder.model_id:
            self.needs_reindex = True
            log.warning(
                "embedding model changed (%s → %s); run `webmind index --rebuild` to re-embed",
                stored,
                self.embedder.model_id,
            )
        tenants = (
            {self.cfg.app.default_tenant}
            | {self.cfg.tenant_of(s) for s in self.cfg.sources}
            | {self.cfg.tenant_of(d) for d in self.cfg.datasets}
        )
        with self.db.session() as s:
            for t in tenants:
                if s.get(Tenant, t) is None:
                    s.add(Tenant(id=t, name=t))
        self._initialized = True

    async def close(self) -> None:
        for comp in (self.llm, self.embedder):
            if comp is not None:
                try:
                    await comp.aclose()
                except Exception:  # noqa: S110 - best-effort cleanup
                    pass
        self.datasets.dispose()
        self.db.dispose()

    # ------------------------------------------------------------- sync
    async def sync(
        self,
        source_id: str | None = None,
        *,
        trigger: str = "manual",
        rebuild: bool = False,
        uris: list[str] | None = None,
    ) -> list[SyncReport]:
        await self.initialize()
        if not self.cfg.sources:
            raise ConfigurationError("no sources configured")
        sources = [self.cfg.source(source_id)] if source_id else list(self.cfg.sources)
        reports = []
        for src in sources:
            reports.append(await self.sync_engine.sync_source(src, trigger=trigger, rebuild=rebuild, uris=uris))
        if rebuild:
            self.db.set_setting("embedding_model", self.embedder.model_id)
            self.needs_reindex = False
        return reports

    async def reembed(self) -> int:
        await self.initialize()
        n = await self.sync_engine.reembed_all()
        self.needs_reindex = False
        return n

    def sync_status(self, tenant_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            counts = dict(
                s.execute(
                    select(Resource.status, func.count())
                    .where(Resource.tenant_id == tenant_id)
                    .group_by(Resource.status)
                ).all()
            )
            sources = []
            for src in self.cfg.sources:
                if self.cfg.tenant_of(src) != tenant_id:
                    continue
                job = s.execute(
                    select(SyncJob).where(SyncJob.connector_id == src.id).order_by(SyncJob.started_at.desc()).limit(1)
                ).scalar_one_or_none()
                sources.append(
                    {
                        "id": src.id,
                        "type": src.type,
                        "last_job": None
                        if job is None
                        else {
                            "status": job.status,
                            "trigger": job.trigger,
                            "started_at": job.started_at.isoformat() if job.started_at else None,
                            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                            "stats": job.stats,
                        },
                    }
                )
        return {
            "resources": counts,
            "sources": sources,
            "embedding_model": self.embedder.model_id,
            "needs_reindex": self.needs_reindex,
        }

    # ------------------------------------------------------------- resources
    def list_resources(self, principal: Principal, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        allowed = set(self.retriever.accessible_resource_ids(principal))
        with self.db.session() as s:
            rows = (
                s.execute(
                    select(Resource)
                    .where(Resource.tenant_id == principal.tenant_id, Resource.status == "active")
                    .order_by(Resource.uri)
                )
                .scalars()
                .all()
            )
            visible = [r for r in rows if r.id in allowed][offset : offset + limit]
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "url": r.public_url,
                    "kind": r.kind,
                    "source": r.connector_id,
                    "version": r.current_version,
                    "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
                    "last_changed_at": r.last_changed_at.isoformat() if r.last_changed_at else None,
                }
                for r in visible
            ]

    def resource_chunks(self, principal: Principal, resource_id: str, limit: int = 20) -> list[RetrievedChunk]:
        with self.db.session() as s:
            res = s.get(Resource, resource_id)
            if (
                res is None
                or res.status != "active"
                or res.tenant_id != principal.tenant_id
                or not self.policy.can_access(principal, res.access, tenant_id=res.tenant_id)
            ):
                raise NotFoundError("resource not found")
            chunks = (
                s.execute(
                    select(Chunk)
                    .where(Chunk.resource_id == res.id, Chunk.version == res.current_version)
                    .order_by(Chunk.ordinal)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            fresh = Freshness(
                last_synced_at=res.last_synced_at, source_changed_at=res.last_changed_at, sync_method=res.sync_method
            )
            return [
                RetrievedChunk(
                    chunk_id=c.id,
                    resource_id=res.id,
                    text=c.text,
                    title=res.title,
                    heading=c.heading,
                    url=res.public_url,
                    anchor_url=deep_link(res.public_url, res.kind, c.anchor, c.heading, c.page),
                    version=c.version,
                    freshness=fresh,
                )
                for c in chunks
            ]
