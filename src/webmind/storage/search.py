"""Keyword and vector search backends.

Every search call receives the *already authorized* set of resource ids. Filtering is
applied inside the query (pre-filtering), so unauthorized content is never scored,
returned, or handed to a language model.
"""

from __future__ import annotations

import math
import re
import struct
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence

from sqlalchemy import bindparam, delete, select, text

from webmind.storage.database import Database
from webmind.storage.models import Chunk, Embedding, Resource

try:  # optional acceleration
    import numpy as _np
except ImportError:  # pragma: no cover - exercised when numpy is absent
    _np = None

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by can do for from has have how i in is it its me my of on or our "
    "the their there this to was what when where which who why will with you your".split()
)


def query_terms(query: str, limit: int = 24) -> list[str]:
    terms: list[str] = []
    for t in _TOKEN_RE.findall(query.lower()):
        if t in _STOP or len(t) < 2 or t in terms:
            continue
        terms.append(t)
    return terms[:limit]


def pack_vector(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def normalize(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec] if n > 0 else list(vec)


# ------------------------------------------------------------------- keyword


class KeywordIndex(ABC):
    @abstractmethod
    def search(self, query: str, *, tenant_id: str, resource_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        """Return (chunk_id, score) pairs, best first. Higher score is better."""


class SqliteFtsKeywordIndex(KeywordIndex):
    """SQLite FTS5 with BM25 ranking (porter stemming)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def search(self, query, *, tenant_id, resource_ids, k):
        terms = query_terms(query)
        if not terms or not resource_ids:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        stmt = text(
            "SELECT c.id AS id, bm25(chunks_fts) AS rank FROM chunks_fts "
            "JOIN chunks c ON c.id = chunks_fts.chunk_id "
            "WHERE chunks_fts MATCH :match AND c.tenant_id = :tenant AND c.resource_id IN :rids "
            "ORDER BY rank LIMIT :k"
        ).bindparams(bindparam("rids", expanding=True))
        with self.db.engine.connect() as conn:
            rows = conn.execute(stmt, {"match": match, "tenant": tenant_id, "rids": list(resource_ids), "k": k}).all()
        return [(r.id, -float(r.rank)) for r in rows]


class PostgresFtsKeywordIndex(KeywordIndex):
    """PostgreSQL full-text search (english stemming, ts_rank_cd)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def search(self, query, *, tenant_id, resource_ids, k):
        terms = query_terms(query)
        if not terms or not resource_ids:
            return []
        tsquery = " | ".join(terms)
        stmt = text(
            "SELECT id, ts_rank_cd(tsv, q) AS rank FROM chunks, to_tsquery('english', :q) q "
            "WHERE tsv @@ q AND tenant_id = :tenant AND resource_id IN :rids "
            "ORDER BY rank DESC LIMIT :k"
        ).bindparams(bindparam("rids", expanding=True))
        with self.db.engine.connect() as conn:
            rows = conn.execute(stmt, {"q": tsquery, "tenant": tenant_id, "rids": list(resource_ids), "k": k}).all()
        return [(r.id, float(r.rank)) for r in rows]


def keyword_index_for(db: Database) -> KeywordIndex:
    return SqliteFtsKeywordIndex(db) if db.dialect == "sqlite" else PostgresFtsKeywordIndex(db)


# -------------------------------------------------------------------- vector


class VectorStore(ABC):
    @abstractmethod
    def initialize(self, dimensions: int) -> None: ...

    @abstractmethod
    def upsert(self, items: Sequence[tuple[str, str, str, Sequence[float]]], model_id: str) -> None:
        """Insert vectors. Items are (chunk_id, tenant_id, resource_id, vector)."""

    @abstractmethod
    def delete(self, chunk_ids: Sequence[str]) -> None: ...

    @abstractmethod
    def search(
        self,
        vector: Sequence[float],
        *,
        tenant_id: str,
        resource_ids: Sequence[str],
        k: int,
        model_id: str,
    ) -> list[tuple[str, float]]:
        """Return (chunk_id, cosine similarity) pairs, best first."""


class DatabaseVectorStore(VectorStore):
    """Exact (brute-force) cosine search over vectors stored in the metadata database.

    Suitable for small and medium sites (tens of thousands of chunks). Vectors are kept
    in a per-tenant in-memory cache that is invalidated whenever the index generation
    changes. Use the pgvector store for larger corpora.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[int, list[str], list[str], object]] = {}

    def initialize(self, dimensions: int) -> None:
        return None

    def upsert(self, items, model_id):
        with self.db.session() as s:
            for chunk_id, _tenant, _rid, vec in items:
                s.merge(
                    Embedding(
                        chunk_id=chunk_id,
                        model_id=model_id,
                        dimensions=len(vec),
                        vector=pack_vector(normalize(vec)),
                    )
                )

    def delete(self, chunk_ids):
        if not chunk_ids:
            return
        with self.db.session() as s:
            s.execute(delete(Embedding).where(Embedding.chunk_id.in_(list(chunk_ids))))

    def _load(self, tenant_id: str, model_id: str):
        gen = self.db.generation(tenant_id)
        key = (tenant_id, model_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == gen:
                return cached
        stmt = (
            select(Embedding.chunk_id, Chunk.resource_id, Embedding.vector)
            .join(Chunk, Chunk.id == Embedding.chunk_id)
            .join(Resource, Resource.id == Chunk.resource_id)
            .where(
                Chunk.tenant_id == tenant_id,
                Embedding.model_id == model_id,
                Resource.status == "active",
                Chunk.version == Resource.current_version,
            )
        )
        with self.db.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        ids = [r[0] for r in rows]
        rids = [r[1] for r in rows]
        if _np is not None:
            matrix: object = (
                _np.vstack([_np.frombuffer(r[2], dtype="<f4") for r in rows])
                if rows
                else _np.zeros((0, 0), dtype="<f4")
            )
        else:
            matrix = [unpack_vector(r[2]) for r in rows]
        entry = (gen, ids, rids, matrix)
        with self._lock:
            self._cache[key] = entry
        return entry

    def search(self, vector, *, tenant_id, resource_ids, k, model_id):
        if not resource_ids:
            return []
        _gen, ids, rids, matrix = self._load(tenant_id, model_id)
        if not ids:
            return []
        allowed = set(resource_ids)
        q = normalize(vector)
        if _np is not None:
            mask = _np.fromiter((r in allowed for r in rids), dtype=bool, count=len(rids))
            if not mask.any():
                return []
            idx = _np.nonzero(mask)[0]
            scores = matrix[idx] @ _np.asarray(q, dtype="<f4")  # type: ignore[index]
            top = _np.argsort(-scores)[:k]
            return [(ids[int(idx[i])], float(scores[i])) for i in top]
        scored = [
            (ids[i], sum(a * b for a, b in zip(matrix[i], q, strict=True)))  # type: ignore[index]
            for i in range(len(ids))
            if rids[i] in allowed
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


class PgVectorStore(VectorStore):
    """pgvector-backed store with an HNSW cosine index (PostgreSQL only)."""

    def __init__(self, db: Database) -> None:
        if db.dialect != "postgresql":
            raise ValueError("PgVectorStore requires PostgreSQL")
        self.db = db
        self.dimensions: int | None = None

    def initialize(self, dimensions: int) -> None:
        self.dimensions = dimensions
        with self.db.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS webmind_vectors ("  # dimensions is an int
                    f" chunk_id VARCHAR(32) PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,"
                    f" tenant_id VARCHAR(64) NOT NULL, resource_id VARCHAR(32) NOT NULL,"
                    f" model_id VARCHAR(255) NOT NULL, embedding vector({int(dimensions)}) NOT NULL)"
                )
            )
            existing = conn.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'webmind_vectors'::regclass "
                    "AND attname = 'embedding'"
                )
            ).scalar()
            if existing is not None and int(existing) != int(dimensions):
                raise ValueError(
                    f"pgvector table has dimension {existing} but the embedding model produces "
                    f"{dimensions}; run `webmind index --rebuild`"
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_webmind_vectors_hnsw ON webmind_vectors "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_webmind_vectors_scope ON webmind_vectors (tenant_id, resource_id)")
            )

    @staticmethod
    def _literal(vec: Sequence[float]) -> str:
        return "[" + ",".join(f"{float(v):.7g}" for v in vec) + "]"

    def upsert(self, items, model_id):
        if not items:
            return
        stmt = text(
            "INSERT INTO webmind_vectors (chunk_id, tenant_id, resource_id, model_id, embedding) "
            "VALUES (:c, :t, :r, :m, CAST(:v AS vector)) ON CONFLICT (chunk_id) DO UPDATE "
            "SET embedding = EXCLUDED.embedding, model_id = EXCLUDED.model_id"
        )
        with self.db.engine.begin() as conn:
            conn.execute(
                stmt,
                [{"c": c, "t": t, "r": r, "m": model_id, "v": self._literal(normalize(v))} for c, t, r, v in items],
            )

    def delete(self, chunk_ids):
        if not chunk_ids:
            return
        stmt = text("DELETE FROM webmind_vectors WHERE chunk_id IN :ids").bindparams(bindparam("ids", expanding=True))
        with self.db.engine.begin() as conn:
            conn.execute(stmt, {"ids": list(chunk_ids)})

    def search(self, vector, *, tenant_id, resource_ids, k, model_id):
        if not resource_ids:
            return []
        stmt = text(
            "SELECT chunk_id, 1 - (embedding <=> CAST(:q AS vector)) AS score FROM webmind_vectors "
            "WHERE tenant_id = :t AND model_id = :m AND resource_id IN :rids "
            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
        ).bindparams(bindparam("rids", expanding=True))
        with self.db.engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {
                    "q": self._literal(normalize(vector)),
                    "t": tenant_id,
                    "m": model_id,
                    "rids": list(resource_ids),
                    "k": k,
                },
            ).all()
        return [(r.chunk_id, float(r.score)) for r in rows]
