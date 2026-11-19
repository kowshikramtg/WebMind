"""Sync and async clients for the WebMind REST API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Union

import httpx

TokenSource = Union[str, Callable[[], "str | None"], None]


class WebMindAPIError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"WebMind API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _headers(token: TokenSource) -> dict[str, str]:
    value = token() if callable(token) else token
    return {"Authorization": f"Bearer {value}"} if value else {}


def _check(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise WebMindAPIError(r.status_code, detail)
    return r.json()


class WebMindClient:
    """Synchronous client. ``token`` may be a string or a callable returning a fresh token."""

    def __init__(
        self,
        base_url: str,
        *,
        token: TokenSource = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token = token
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def __enter__(self) -> WebMindClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return _check(self._http.post(path, json=body, headers=_headers(self.token)))

    def _get(self, path: str, **params: Any) -> Any:
        return _check(self._http.get(path, params=params or None, headers=_headers(self.token)))

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def chat(self, message: str, conversation_id: str | None = None) -> dict[str, Any]:
        return self._post("/v1/chat", {"message": message, "conversation_id": conversation_id})

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        return self._post("/v1/search", {"query": query, "top_k": top_k})["results"]

    def analytics(self, kind: str, query: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/analytics/query", {"kind": kind, "query": query})["table"]

    def export(self, query: dict[str, Any], format: str = "csv", title: str | None = None) -> dict[str, Any]:
        return self._post("/v1/exports", {"query": query, "format": format, "title": title})

    def download(self, export: dict[str, Any]) -> bytes:
        r = self._http.get(export["download_url"], headers=_headers(self.token))
        if r.status_code >= 400:
            raise WebMindAPIError(r.status_code, r.text)
        return r.content

    def resources(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        return self._get("/v1/resources", limit=limit, offset=offset)["items"]


class AsyncWebMindClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: TokenSource = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token = token
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def __aenter__(self) -> AsyncWebMindClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        return _check(await self._http.post(path, json=body, headers=_headers(self.token)))

    async def health(self) -> dict[str, Any]:
        return _check(await self._http.get("/v1/health"))

    async def chat(self, message: str, conversation_id: str | None = None) -> dict[str, Any]:
        return await self._post("/v1/chat", {"message": message, "conversation_id": conversation_id})

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        return (await self._post("/v1/search", {"query": query, "top_k": top_k}))["results"]

    async def analytics(self, kind: str, query: dict[str, Any]) -> dict[str, Any]:
        return (await self._post("/v1/analytics/query", {"kind": kind, "query": query}))["table"]

    async def export(self, query: dict[str, Any], format: str = "csv", title: str | None = None) -> dict[str, Any]:
        return await self._post("/v1/exports", {"query": query, "format": format, "title": title})

    async def stream(self, message: str, conversation_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield server-sent events: {"type": "plan" | "token" | "final" | "error", ...}."""
        async with self._http.stream(
            "POST",
            "/v1/chat/stream",
            json={"message": message, "conversation_id": conversation_id},
            headers=_headers(self.token),
        ) as r:
            if r.status_code >= 400:
                await r.aread()
                raise WebMindAPIError(r.status_code, r.text)
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    yield json.loads(line[5:].strip())
