"""Permission-aware hybrid retrieval (semantic + keyword → RRF → optional rerank)."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote

from sqlalchemy import select

from webmind.core.config import WebMindConfig
from webmind.core.types import Freshness, Principal, RetrievedChunk
from webmind.providers.base import EmbeddingProvider, Reranker
from webmind.security.policy import PolicyEngine
from webmind.storage.database import Database
from webmind.storage.models import Chunk, Resource
from webmind.storage.search import KeywordIndex, VectorStore, query_terms

MAX_CHUNKS_PER_RESOURCE = 3
HEADING_BOOST = 1.0


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s", "ing", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def deep_link(url: str | None, kind: str, anchor: str | None, heading: str | None, page: int | None) -> str | None:
    if not url:
        return None
    if kind == "pdf" and page:
        return f"{url}#page={page}"
    if anchor and kind in ("html", "markdown"):
        return f"{url}#{quote(anchor, safe='-_.~')}"
    if heading and kind == "html":
        # Scroll-to-Text Fragment: supported by Chromium/Safari, ignored elsewhere.
        return f"{url}#:~:text={quote(heading[:80], safe='')}"
    return url


class Retriever:
    def __init__(
        self,
        cfg: WebMindConfig,
        db: Database,
        policy: PolicyEngine,
        embedder: EmbeddingProvider,
        vectors: VectorStore,
        keyword: KeywordIndex,
        reranker: Reranker | None = None,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.policy = policy
        self.embedder = embedder
        self.vectors = vectors
        self.keyword = keyword
        self.reranker = reranker
        self.last_timing_ms: float = 0.0

    def accessible_resource_ids(self, principal: Principal) -> list[str]:
        """Resources in the principal's tenant whose access rule admits the principal."""
        with self.db.engine.connect() as conn:
            rows = conn.execute(
                select(Resource.id, Resource.access).where(
                    Resource.tenant_id == principal.tenant_id, Resource.status == "active"
                )
            ).all()
        return [rid for rid, access in rows if self.policy.can_access(principal, access, tenant_id=principal.tenant_id)]

    async def search(self, principal: Principal, query: str, *, k: int | None = None) -> list[RetrievedChunk]:
        started = time.perf_counter()
        rcfg = self.cfg.retrieval
        k = k or rcfg.top_k
        allowed = await asyncio.to_thread(self.accessible_resource_ids, principal)
        if not allowed or not query.strip():
            return []
        tenant = principal.tenant_id

        qvec = await self.embedder.embed_query(query)
        semantic = await asyncio.to_thread(
            self.vectors.search,
            qvec,
            tenant_id=tenant,
            resource_ids=allowed,
            k=rcfg.candidate_k,
            model_id=self.embedder.model_id,
        )
        semantic = [(cid, s) for cid, s in semantic if s >= rcfg.min_semantic_score]
        keyword: list[tuple[str, float]] = []
        if rcfg.hybrid_search:
            keyword = await asyncio.to_thread(
                self.keyword.search, query, tenant_id=tenant, resource_ids=allowed, k=rcfg.candidate_k
            )

        fused: dict[str, float] = {}
        sem_scores = dict(semantic)
        kw_scores = dict(keyword)
        for ranked in (semantic, keyword):
            for rank, (cid, _score) in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (rcfg.rrf_k + rank + 1)
        if not fused:
            self.last_timing_ms = (time.perf_counter() - started) * 1000
            return []
        candidate_ids = sorted(fused, key=fused.get, reverse=True)[: rcfg.candidate_k]  # type: ignore[arg-type]

        rows = await asyncio.to_thread(self._load, candidate_ids, tenant)
        # Structural signal: chunks whose section heading matches the query terms are
        # usually the precise answer to "where is X" questions.
        terms = {_stem(t) for t in query_terms(query)}
        if terms:
            for cid in candidate_ids:
                row = rows.get(cid)
                if row is None:
                    continue
                heading_terms = {_stem(t) for t in query_terms(row[0].heading or "")}
                overlap = len(terms & heading_terms) / len(terms)
                fused[cid] += overlap * HEADING_BOOST / (rcfg.rrf_k + 1)
            candidate_ids.sort(key=lambda c: fused[c], reverse=True)
        allowed_set = set(allowed)
        results: list[RetrievedChunk] = []
        for cid in candidate_ids:
            row = rows.get(cid)
            if row is None:
                continue
            chunk, res = row
            if res.id not in allowed_set or res.tenant_id != tenant:  # defense in depth
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    resource_id=res.id,
                    text=chunk.text,
                    title=res.title,
                    heading=chunk.heading,
                    url=res.public_url,
                    anchor_url=deep_link(res.public_url, res.kind, chunk.anchor, chunk.heading, chunk.page),
                    score=fused[cid],
                    semantic_score=sem_scores.get(cid),
                    keyword_score=kw_scores.get(cid),
                    version=chunk.version,
                    freshness=Freshness(
                        mode="indexed",
                        last_synced_at=res.last_synced_at,
                        source_changed_at=res.last_changed_at,
                        sync_method=res.sync_method,
                    ),
                )
            )

        if self.reranker is not None and results:
            scores = await self.reranker.score(query, [f"{r.title or ''} {r.heading or ''}\n{r.text}" for r in results])
            for r, s in zip(results, scores, strict=True):
                r.score = s
            results.sort(key=lambda r: r.score, reverse=True)

        per_resource: dict[str, int] = {}
        final: list[RetrievedChunk] = []
        for r in results:
            if per_resource.get(r.resource_id, 0) >= MAX_CHUNKS_PER_RESOURCE:
                continue
            per_resource[r.resource_id] = per_resource.get(r.resource_id, 0) + 1
            final.append(r)
            if len(final) >= k:
                break
        self.last_timing_ms = (time.perf_counter() - started) * 1000
        return final

    def _load(self, chunk_ids: list[str], tenant: str) -> dict[str, tuple[Chunk, Resource]]:
        with self.db.session() as s:
            rows = s.execute(
                select(Chunk, Resource)
                .join(Resource, Resource.id == Chunk.resource_id)
                .where(
                    Chunk.id.in_(chunk_ids),
                    Chunk.tenant_id == tenant,
                    Resource.status == "active",
                    Chunk.version == Resource.current_version,
                )
            ).all()
        # expire_on_commit=False keeps loaded attributes usable after the session closes.
        return {c.id: (c, r) for c, r in rows}
