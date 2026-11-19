"""In-process Python SDK.

Use this when your Python backend (FastAPI, Django, Flask, scripts) wants WebMind's
capabilities without running a separate server::

    from webmind import WebMind

    assistant = WebMind(config="webmind.yaml")
    await assistant.initialize()
    response = await assistant.ask(user_id="user_123", roles=["employee"],
                                   attributes={"department": "engineering"},
                                   query="Explain the latest project updates")
    print(response.answer)

Identity passed to ``ask`` is trusted: your backend is responsible for having
authenticated the user. For browser or cross-service access, run the HTTP server and
use signed tokens instead (see INTEGRATION.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from webmind.core.config import WebMindConfig, load_config
from webmind.core.types import ChatResponse, Principal, RetrievedChunk
from webmind.ingestion.sync import SyncReport
from webmind.runtime import WebMindRuntime


class WebMind:
    def __init__(self, config: str | Path | WebMindConfig = "webmind.yaml", **runtime_kwargs: Any) -> None:
        self.config = config if isinstance(config, WebMindConfig) else load_config(config)
        self.runtime = WebMindRuntime(self.config, **runtime_kwargs)

    async def initialize(self) -> None:
        await self.runtime.initialize()

    async def __aenter__(self) -> WebMind:
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def principal(
        self,
        user_id: str | None = None,
        *,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> Principal:
        """Build a principal for a user your application has already authenticated.
        ``user_id=None`` means an anonymous visitor (configured anonymous roles)."""
        if user_id is None:
            return self.runtime.identity.anonymous()
        return self.runtime.policy.build_principal(
            tenant_id=tenant_id or self.config.app.default_tenant,
            user_id=user_id,
            roles=roles or [],
            attributes=attributes or {},
            authenticated=True,
        )

    async def ask(
        self,
        query: str,
        user_id: str | None = None,
        *,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
    ) -> ChatResponse:
        p = self.principal(user_id, roles=roles, attributes=attributes, tenant_id=tenant_id)
        return await self.runtime.orchestrator.chat(p, query, conversation_id)

    async def ask_stream(self, query: str, user_id: str | None = None, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        conversation_id = kwargs.pop("conversation_id", None)
        p = self.principal(user_id, **kwargs)
        async for ev in self.runtime.orchestrator.chat_stream(p, query, conversation_id):
            yield ev

    async def search(
        self, query: str, user_id: str | None = None, *, top_k: int | None = None, **kwargs: Any
    ) -> list[RetrievedChunk]:
        p = self.principal(user_id, **kwargs)
        return await self.runtime.retriever.search(p, query, k=top_k)

    async def sync(self, source_id: str | None = None, *, rebuild: bool = False) -> list[SyncReport]:
        return await self.runtime.sync(source_id, rebuild=rebuild)

    async def close(self) -> None:
        await self.runtime.close()
