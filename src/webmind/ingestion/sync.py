"""Continuous synchronization engine.

New resource      → extract → normalize → fingerprint → version → chunk → embed → index
Changed resource  → compare fingerprint → new version → re-chunk/re-embed → swap index
Unchanged         → touch sync metadata only (conditional fetch / fingerprint match)
Deleted resource  → mark deleted → remove from active retrieval → retention applies

Each resource is processed in its own transaction, so an interrupted sync leaves the
index consistent and the next run resumes by fingerprint comparison.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select

from webmind.core.config import FilesystemSourceConfig, SourceConfig, WebMindConfig, WebsiteSourceConfig
from webmind.core.errors import ConnectorError
from webmind.ingestion.chunking import chunk_document, embedding_text
from webmind.ingestion.connectors.base import FetchResult, PreviousState, ResourceRef, SourceConnector
from webmind.ingestion.connectors.filesystem import FilesystemConnector
from webmind.ingestion.connectors.website import WebsiteConnector
from webmind.ingestion.parsers import detect_kind, parse_document
from webmind.providers.base import EmbeddingProvider
from webmind.storage.database import Database
from webmind.storage.models import (
    Chunk,
    Connector,
    Document,
    EmbeddingCache,
    Resource,
    ResourceVersion,
    SyncJob,
    utcnow,
)
from webmind.storage.object_store import ContentStore, sha256_hex
from webmind.storage.search import VectorStore, normalize, pack_vector, unpack_vector

log = logging.getLogger("webmind.sync")


@dataclass
class SyncReport:
    source_id: str
    job_id: str
    status: str = "running"
    discovered: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    not_modified: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    error_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def build_connector(cfg: WebMindConfig, source: SourceConfig) -> SourceConnector:
    max_bytes = cfg.ingestion.max_document_bytes
    if isinstance(source, WebsiteSourceConfig):
        return WebsiteConnector(source, max_bytes)
    if isinstance(source, FilesystemSourceConfig):
        return FilesystemConnector(source, cfg.resolve_path(source.path), max_bytes)
    raise ConnectorError(f"unsupported source type: {source.type}")  # pragma: no cover


def access_for(source: SourceConfig, uri: str, public_url: str | None) -> dict[str, Any]:
    """Resolve the access rule for a resource: first matching path rule, else source default."""
    for rule in source.path_rules:
        rx = re.compile(rule.pattern)
        if rx.search(uri) or (public_url and rx.search(public_url)):
            return rule.access.model_dump()
    return source.access.model_dump()


class SyncEngine:
    def __init__(
        self,
        cfg: WebMindConfig,
        db: Database,
        cas: ContentStore,
        embedder: EmbeddingProvider,
        vectors: VectorStore,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.cas = cas
        self.embedder = embedder
        self.vectors = vectors

    # ------------------------------------------------------------- embeddings
    async def embed_chunks(self, texts: list[str], hashes: list[str]) -> list[list[float]]:
        """Embed with a content-keyed cache so unchanged chunk text is never re-embedded."""
        model_id = self.embedder.model_id
        with self.db.session() as s:
            rows = s.execute(
                select(EmbeddingCache.content_hash, EmbeddingCache.vector).where(
                    EmbeddingCache.model_id == model_id, EmbeddingCache.content_hash.in_(set(hashes))
                )
            ).all()
        cached = {h: unpack_vector(v) for h, v in rows}
        missing = [i for i, h in enumerate(hashes) if h not in cached]
        if missing:
            fresh = await self.embedder.embed_documents([texts[i] for i in missing])
            with self.db.session() as s:
                for i, vec in zip(missing, fresh, strict=True):
                    h = hashes[i]
                    if h not in cached:
                        cached[h] = vec
                        s.merge(EmbeddingCache(content_hash=h, model_id=model_id, vector=pack_vector(normalize(vec))))
        return [cached[h] for h in hashes]

    # ----------------------------------------------------------------- public
    async def sync_source(
        self,
        source: SourceConfig,
        *,
        trigger: str = "manual",
        uris: list[str] | None = None,
        rebuild: bool = False,
        connector: SourceConnector | None = None,
    ) -> SyncReport:
        tenant = self.cfg.tenant_of(source)
        started = time.perf_counter()
        job = SyncJob(tenant_id=tenant, connector_id=source.id, trigger=trigger)
        with self.db.session() as s:
            s.add(job)
            conn_row = s.get(Connector, source.id)
            cfg_hash = sha256_hex(source.model_dump_json().encode())
            if conn_row is None:
                s.add(Connector(id=source.id, tenant_id=tenant, type=source.type, config_hash=cfg_hash))
            else:
                if conn_row.tenant_id != tenant:
                    raise ConnectorError(f"source '{source.id}' already belongs to tenant '{conn_row.tenant_id}'")
                conn_row.config_hash = cfg_hash
        report = SyncReport(source_id=source.id, job_id=job.id)
        own_connector = connector is None
        connector = connector or build_connector(self.cfg, source)
        try:
            with self.db.session() as s:
                existing = {
                    r.uri: r
                    for r in s.execute(
                        select(Resource).where(Resource.tenant_id == tenant, Resource.connector_id == source.id)
                    ).scalars()
                }
            previous = {
                uri: PreviousState(
                    etag=r.etag,
                    last_modified=r.last_modified,
                    change_hint=r.change_hint,
                    content_hash=r.content_hash,
                    links=list(r.links or []),
                )
                for uri, r in existing.items()
                if r.status == "active" and not rebuild
            }
            connector.prepare(previous)

            seen: set[str] = set()
            if uris:  # targeted (webhook) sync
                refs = [ResourceRef(uri=u) for u in uris]
                for ref in refs:
                    seen.add(ref.uri)
                    await self._handle(connector, source, tenant, ref, previous.get(ref.uri), report, rebuild)
            else:
                async for ref in connector.discover():
                    report.discovered += 1
                    seen.add(ref.uri)
                    await self._handle(connector, source, tenant, ref, previous.get(ref.uri), report, rebuild)
                # Resources that disappeared from the source.
                for uri, res in existing.items():
                    if uri in seen or res.status != "active":
                        continue
                    gone = connector.discovery_complete or await connector.confirm_missing(uri)
                    if gone:
                        self._mark_deleted(res.id, tenant)
                        report.deleted += 1
            report.status = "partial" if report.errors else "succeeded"
        except Exception as exc:
            report.status = "failed"
            report.errors += 1
            report.error_samples.append(f"{type(exc).__name__}: {exc}"[:500])
            log.exception("sync of source %s failed", source.id)
        finally:
            if own_connector:
                await connector.aclose()
            report.duration_ms = round((time.perf_counter() - started) * 1000, 1)
            with self.db.session() as s:
                row = s.get(SyncJob, job.id)
                if row is not None:
                    row.status = report.status
                    row.finished_at = utcnow()
                    row.stats = report.as_dict()
                    row.error = "; ".join(report.error_samples) or None
                conn_row = s.get(Connector, source.id)
                if conn_row is not None and report.status != "failed":
                    conn_row.last_sync_at = utcnow()
            self.db.bump_generation(tenant)
        return report

    # ---------------------------------------------------------------- internals
    async def _handle(
        self,
        connector: SourceConnector,
        source: SourceConfig,
        tenant: str,
        ref: ResourceRef,
        previous: PreviousState | None,
        report: SyncReport,
        rebuild: bool,
    ) -> None:
        try:
            result = await connector.fetch(ref, previous)
        except Exception as exc:  # connector bug — record and continue
            result = FetchResult(status="error", uri=ref.uri, error=f"{type(exc).__name__}: {exc}")
        if result.status == "not_modified":
            self._touch(tenant, source.id, ref.uri, result)
            report.not_modified += 1
            return
        if result.status == "gone":
            res_id = self._resource_id(tenant, source.id, ref.uri)
            if res_id:
                self._mark_deleted(res_id, tenant)
                report.deleted += 1
            return
        if result.status == "error":
            report.errors += 1
            if len(report.error_samples) < 10:
                report.error_samples.append(f"{ref.uri}: {result.error}")
            self._record_error(tenant, source.id, ref.uri, result.error or "error")
            return
        try:
            outcome = await self._ingest(source, tenant, result, rebuild=rebuild)
        except Exception as exc:
            log.exception("failed to ingest %s", ref.uri)
            report.errors += 1
            if len(report.error_samples) < 10:
                report.error_samples.append(f"{ref.uri}: {type(exc).__name__}: {exc}"[:500])
            self._record_error(tenant, source.id, ref.uri, f"{type(exc).__name__}: {exc}")
            return
        setattr(report, outcome, getattr(report, outcome) + 1)

    def _resource_id(self, tenant: str, source_id: str, uri: str) -> str | None:
        with self.db.session() as s:
            return s.execute(
                select(Resource.id).where(
                    Resource.tenant_id == tenant, Resource.connector_id == source_id, Resource.uri == uri
                )
            ).scalar_one_or_none()

    def _touch(self, tenant: str, source_id: str, uri: str, result: FetchResult) -> None:
        with self.db.session() as s:
            res = s.execute(
                select(Resource).where(
                    Resource.tenant_id == tenant, Resource.connector_id == source_id, Resource.uri == uri
                )
            ).scalar_one_or_none()
            if res:
                res.last_synced_at = utcnow()
                res.error = None

    def _record_error(self, tenant: str, source_id: str, uri: str, error: str) -> None:
        # Errors never delete or hide existing indexed content; we only annotate it.
        with self.db.session() as s:
            res = s.execute(
                select(Resource).where(
                    Resource.tenant_id == tenant, Resource.connector_id == source_id, Resource.uri == uri
                )
            ).scalar_one_or_none()
            if res:
                res.error = error[:2000]

    def _mark_deleted(self, resource_id: str, tenant: str) -> None:
        with self.db.session() as s:
            res = s.get(Resource, resource_id)
            if res is None or res.tenant_id != tenant:
                return
            chunk_ids = list(s.execute(select(Chunk.id).where(Chunk.resource_id == resource_id)).scalars())
            s.execute(delete(Chunk).where(Chunk.resource_id == resource_id))
            res.status = "deleted"
            res.deleted_at = utcnow()
            res.last_synced_at = utcnow()
        self.vectors.delete(chunk_ids)

    async def _ingest(self, source: SourceConfig, tenant: str, result: FetchResult, *, rebuild: bool) -> str:
        assert result.content is not None
        uri = result.uri
        doc = parse_document(result.content, content_type=result.content_type, uri=uri)
        if doc is None:
            return "skipped"
        with self.db.session() as s:
            res = s.execute(
                select(Resource).where(
                    Resource.tenant_id == tenant, Resource.connector_id == source.id, Resource.uri == uri
                )
            ).scalar_one_or_none()
            res_id = res.id if res else None
            res_status = res.status if res else None
            res_hash = res.content_hash if res else None
        if doc.noindex:
            if res_id and res_status == "active":
                self._mark_deleted(res_id, tenant)
                return "deleted"
            return "skipped"

        title = doc.title or uri.rsplit("/", 1)[-1] or uri
        normalized = f"{title}\n\n{doc.text}"
        text_hash = sha256_hex(normalized.encode("utf-8"))
        access = access_for(source, uri, result.public_url)
        links = doc.links[:1000] if detect_kind(result.content_type, uri) == "html" else []

        if res_id and res_status == "active" and res_hash == text_hash and not rebuild:
            with self.db.session() as s:
                r = s.get(Resource, res_id)
                assert r is not None
                r.last_synced_at = utcnow()
                r.etag, r.last_modified, r.change_hint = result.etag, result.last_modified, result.change_hint
                r.access = access  # policy changes apply without re-indexing
                r.links = links
                r.error = None
            return "unchanged"

        # --- new or changed content: build the new representation first -------
        drafts = chunk_document(doc, size=self.cfg.ingestion.chunk_size, overlap=self.cfg.ingestion.chunk_overlap)
        embed_inputs = [embedding_text(title, d) for d in drafts]
        hashes = [sha256_hex(t.encode("utf-8")) for t in embed_inputs]
        vectors = await self.embed_chunks(embed_inputs, hashes) if drafts else []

        original = self.cas.put(result.content) if self.cfg.ingestion.store_originals else None
        self.cas.put(normalized.encode("utf-8"))
        now = utcnow()
        is_new = res_id is None
        with self.db.session() as s:
            if res_id is None:
                r = Resource(tenant_id=tenant, connector_id=source.id, uri=uri, first_seen_at=now)
                s.add(r)
                s.flush()
            else:
                r = s.get(Resource, res_id)
                assert r is not None
            old_chunks = list(s.execute(select(Chunk.id).where(Chunk.resource_id == r.id)).scalars())
            s.execute(delete(Chunk).where(Chunk.resource_id == r.id))
            version_no = (r.current_version or 0) + 1
            ver = ResourceVersion(
                resource_id=r.id,
                version=version_no,
                content_hash=text_hash,
                original_hash=original.sha256 if original else None,
                original_size=len(result.content),
                content_type=result.content_type,
            )
            s.add(ver)
            s.flush()
            s.add(
                Document(
                    version_id=ver.id,
                    title=title,
                    description=doc.description,
                    text_hash=text_hash,
                    section_count=len(doc.sections),
                    char_count=len(doc.text),
                )
            )
            new_chunks: list[Chunk] = []
            for d, h in zip(drafts, hashes, strict=True):
                c = Chunk(
                    tenant_id=tenant,
                    resource_id=r.id,
                    version=version_no,
                    ordinal=d.ordinal,
                    heading=d.heading,
                    heading_path=d.heading_path,
                    anchor=d.anchor,
                    page=d.page,
                    text=d.text,
                    content_hash=h,
                    char_count=len(d.text),
                )
                new_chunks.append(c)
            # Update the title before chunk inserts so the FTS trigger indexes it.
            r.title = title[:1024]
            s.flush()
            s.add_all(new_chunks)
            s.flush()
            r.kind = detect_kind(result.content_type, uri) or "text"
            r.public_url = result.public_url
            r.status = "active"
            r.deleted_at = None
            r.access = access
            r.current_version = version_no
            r.content_hash = text_hash
            r.etag, r.last_modified, r.change_hint = result.etag, result.last_modified, result.change_hint
            r.sync_method = source.type
            r.last_synced_at = now
            r.last_changed_at = now
            r.last_indexed_at = now
            r.links = links
            r.error = None
            items = [(c.id, tenant, r.id, v) for c, v in zip(new_chunks, vectors, strict=True)]
            resource_id = r.id
        self.vectors.delete(old_chunks)
        self.vectors.upsert(items, self.embedder.model_id)
        self._apply_retention(resource_id)
        return "new" if is_new else "changed"

    def _apply_retention(self, resource_id: str) -> None:
        keep = self.cfg.retention.keep_versions
        with self.db.session() as s:
            versions = list(
                s.execute(
                    select(ResourceVersion)
                    .where(ResourceVersion.resource_id == resource_id)
                    .order_by(ResourceVersion.version.desc())
                ).scalars()
            )
            stale = versions[keep:]
            hashes: set[str] = set()
            for v in stale:
                if v.original_hash:
                    hashes.add(v.original_hash)
                s.delete(v)
        # Garbage-collect blobs no longer referenced by any version (dedup-aware).
        for h in hashes:
            with self.db.session() as s:
                refs = s.execute(
                    select(func.count()).select_from(ResourceVersion).where(ResourceVersion.original_hash == h)
                ).scalar_one()
            if refs == 0:
                self.cas.delete(h)

    # ------------------------------------------------------- re-embedding
    async def reembed_all(self, tenant_id: str | None = None) -> int:
        """Re-embed every active chunk with the current embedding model (no re-fetch needed)."""
        with self.db.session() as s:
            q = (
                select(Chunk, Resource.title)
                .join(Resource, Resource.id == Chunk.resource_id)
                .where(Resource.status == "active")
            )
            if tenant_id:
                q = q.where(Chunk.tenant_id == tenant_id)
            rows = [(c.id, c.tenant_id, c.resource_id, c.text, c.heading_path, t) for c, t in s.execute(q).all()]
        from webmind.ingestion.chunking import ChunkDraft

        total = 0
        tenants: set[str] = set()
        for i in range(0, len(rows), 256):
            batch = rows[i : i + 256]
            texts = [
                embedding_text(t, ChunkDraft(ordinal=0, text=txt, heading=None, heading_path=list(hp or [])))
                for _, _, _, txt, hp, t in batch
            ]
            hashes = [sha256_hex(t.encode("utf-8")) for t in texts]
            vecs = await self.embed_chunks(texts, hashes)
            self.vectors.upsert(
                [(cid, ten, rid, v) for (cid, ten, rid, _, _, _), v in zip(batch, vecs, strict=True)],
                self.embedder.model_id,
            )
            tenants.update(ten for _, ten, *_ in batch)
            total += len(batch)
        for t in tenants:
            self.db.bump_generation(t)
        self.db.set_setting("embedding_model", self.embedder.model_id)
        return total


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
