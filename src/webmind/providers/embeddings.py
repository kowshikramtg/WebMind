"""Embedding providers and rerankers."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

import httpx

from webmind.core.config import EmbeddingsConfig, RerankingConfig
from webmind.core.errors import ProviderError, ProviderNotInstalledError
from webmind.providers.base import EmbeddingProvider, Reranker

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by can do for from has have how i in is it its of on or our that the "
    "their there these this to was we what when where which who why will with you your".split()
)


def _stem(word: str) -> str:
    for suffix in ("ingly", "edly", "ing", "ies", "ied", "ed", "es", "s", "ly"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return word


class HashingEmbedder(EmbeddingProvider):
    """Dependency-free lexical embedding via signed feature hashing.

    Features: stemmed unigrams, bigrams and character 4-grams with sublinear TF. This is a
    *lexical* baseline that lets WebMind run with zero model infrastructure; it does not
    capture synonyms or paraphrases. Configure a neural embedding provider (Ollama,
    OpenAI-compatible, sentence-transformers) for true semantic retrieval.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 512) -> None:
        self._dims = dimensions

    @property
    def model_id(self) -> str:
        return f"webmind-hashing-v1:{self._dims}"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _features(self, text: str) -> dict[str, float]:
        words = [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]
        feats: dict[str, float] = {}
        for w in words:
            feats["u:" + w] = feats.get("u:" + w, 0.0) + 1.0
            padded = f"#{w}#"
            for i in range(max(1, len(padded) - 3)):
                g = "c:" + padded[i : i + 4]
                feats[g] = feats.get(g, 0.0) + 0.25
        for a, b in zip(words, words[1:], strict=False):
            k = f"b:{a}_{b}"
            feats[k] = feats.get(k, 0.0) + 1.0
        return feats

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dims
        for feat, tf in self._features(text).items():
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
            idx = h % self._dims
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(tf)) if tf >= 1 else sign * tf
        n = math.sqrt(sum(v * v for v in vec))
        return [v / n for v in vec] if n else vec

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OllamaEmbedder(EmbeddingProvider):
    name = "ollama"

    def __init__(self, cfg: EmbeddingsConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds)
        self._dims = cfg.dimensions

    @property
    def model_id(self) -> str:
        return f"ollama:{self.cfg.model}"

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            raise ProviderError("embedding dimensions unknown until the first embedding call")
        return self._dims

    async def embed_documents(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), self.cfg.batch_size):
            batch = list(texts[i : i + self.cfg.batch_size])
            try:
                r = await self._client.post(
                    f"{self.base_url}/api/embed", json={"model": self.cfg.model, "input": batch}
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"ollama embeddings failed: {exc}") from exc
            out.extend(r.json()["embeddings"])
        if out:
            self._dims = len(out[0])
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatibleEmbedder(EmbeddingProvider):
    name = "openai"

    def __init__(self, cfg: EmbeddingsConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {}
        if cfg.api_key is not None:
            headers["Authorization"] = f"Bearer {cfg.api_key.get_secret_value()}"
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds, headers=headers)
        self._dims = cfg.dimensions

    @property
    def model_id(self) -> str:
        suffix = f":{self.cfg.dimensions}" if self.cfg.dimensions else ""
        return f"openai:{self.cfg.model}{suffix}"

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            raise ProviderError("embedding dimensions unknown until the first embedding call")
        return self._dims

    async def embed_documents(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), self.cfg.batch_size):
            payload: dict[str, Any] = {"model": self.cfg.model, "input": list(texts[i : i + self.cfg.batch_size])}
            if self.cfg.dimensions:
                payload["dimensions"] = self.cfg.dimensions
            try:
                r = await self._client.post(f"{self.base_url}/embeddings", json=payload)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"embeddings request failed: {exc}") from exc
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        if out:
            self._dims = len(out[0])
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


class SentenceTransformersEmbedder(EmbeddingProvider):
    name = "sentence_transformers"

    def __init__(self, cfg: EmbeddingsConfig) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ProviderNotInstalledError("sentence_transformers", "local-embeddings") from exc
        self.cfg = cfg
        self._model = SentenceTransformer(cfg.model)
        self._dims = int(self._model.get_sentence_embedding_dimension())

    @property
    def model_id(self) -> str:
        return f"st:{self.cfg.model}"

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed_documents(self, texts):
        vecs = await asyncio.to_thread(
            self._model.encode, list(texts), batch_size=self.cfg.batch_size, normalize_embeddings=True
        )
        return [list(map(float, v)) for v in vecs]


class CrossEncoderReranker(Reranker):
    name = "cross_encoder"

    def __init__(self, cfg: RerankingConfig) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ProviderNotInstalledError("cross_encoder", "rerank") from exc
        self._model = CrossEncoder(cfg.model)

    async def score(self, query, passages):
        pairs = [(query, p) for p in passages]
        scores = await asyncio.to_thread(self._model.predict, pairs)
        return [float(s) for s in scores]


def create_embedder(cfg: EmbeddingsConfig) -> EmbeddingProvider:
    if cfg.provider == "hashing":
        return HashingEmbedder(cfg.dimensions or 512)
    if cfg.provider == "ollama":
        return OllamaEmbedder(cfg)
    if cfg.provider == "openai":
        return OpenAICompatibleEmbedder(cfg)
    if cfg.provider == "sentence_transformers":
        return SentenceTransformersEmbedder(cfg)
    raise ProviderError(f"unknown embeddings provider: {cfg.provider}")  # pragma: no cover


def create_reranker(cfg: RerankingConfig) -> Reranker | None:
    if cfg.provider == "none":
        return None
    return CrossEncoderReranker(cfg)
