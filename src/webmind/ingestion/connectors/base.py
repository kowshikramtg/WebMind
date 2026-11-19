"""Source connector contract.

A connector knows how to *discover* resources in a source and *fetch* their current
content. Connectors are read-only by construction: the interface has no write methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ResourceRef(BaseModel):
    uri: str
    title: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    # Cheap change hint supplied during discovery (e.g. mtime+size); optional.
    change_hint: str | None = None


class PreviousState(BaseModel):
    """What WebMind already knows about a resource, used for conditional fetches."""

    etag: str | None = None
    last_modified: str | None = None
    change_hint: str | None = None
    content_hash: str | None = None
    links: list[str] = []


class FetchResult(BaseModel):
    status: Literal["ok", "not_modified", "gone", "error"]
    uri: str
    content: bytes | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    change_hint: str | None = None
    public_url: str | None = None
    error: str | None = None
    fetched_at: datetime | None = None


class SourceConnector(ABC):
    type: str = "abstract"

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        # Set by discover(); when True the discovery covered the whole source, so
        # resources that were not seen can be treated as deleted.
        self.discovery_complete = False
        self.previous: dict[str, PreviousState] = {}

    @abstractmethod
    def discover(self) -> AsyncIterator[ResourceRef]:
        """Yield references to all resources currently available in the source."""

    @abstractmethod
    async def fetch(self, ref: ResourceRef, previous: PreviousState | None = None) -> FetchResult:
        """Fetch a resource, using ``previous`` for conditional requests where supported."""

    def prepare(self, previous: dict[str, PreviousState]) -> None:
        """Receive known state for all resources of this source before discovery."""
        self.previous = previous

    async def confirm_missing(self, uri: str) -> bool:
        """Return True only if the resource is definitively gone from the source."""
        result = await self.fetch(ResourceRef(uri=uri))
        return result.status == "gone"

    async def aclose(self) -> None:
        return None
