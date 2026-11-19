"""Local filesystem connector (read-only)."""

from __future__ import annotations

import asyncio
import fnmatch
import mimetypes
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from webmind.core.config import FilesystemSourceConfig
from webmind.ingestion.connectors.base import FetchResult, PreviousState, ResourceRef, SourceConnector


class FilesystemConnector(SourceConnector):
    type = "filesystem"

    def __init__(self, cfg: FilesystemSourceConfig, root: Path, max_bytes: int) -> None:
        super().__init__(cfg.id)
        self.cfg = cfg
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def _resolve(self, uri: str) -> Path | None:
        p = (self.root / uri).resolve()
        # Never follow paths (including symlinks) that escape the configured root.
        if p != self.root and self.root not in p.parents:
            return None
        return p

    def _excluded(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) for pat in self.cfg.exclude)

    @staticmethod
    def _hint(p: Path) -> str:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"

    def public_url(self, uri: str) -> str | None:
        if not self.cfg.base_url:
            return None
        return self.cfg.base_url.rstrip("/") + "/" + quote(uri)

    def _scan(self) -> list[tuple[str, str]]:
        found: dict[str, str] = {}
        for pattern in self.cfg.patterns:
            for p in self.root.glob(pattern):
                if not p.is_file():
                    continue
                resolved = p.resolve()
                if self.root not in resolved.parents:
                    continue
                rel = p.relative_to(self.root).as_posix()
                if self._excluded(rel) or any(part.startswith(".") for part in Path(rel).parts):
                    continue
                found[rel] = self._hint(p)
        return sorted(found.items())

    async def discover(self) -> AsyncIterator[ResourceRef]:
        self.discovery_complete = False
        if not self.root.is_dir():
            raise FileNotFoundError(f"filesystem source '{self.source_id}': {self.root} is not a directory")
        entries = await asyncio.to_thread(self._scan)
        for rel, hint in entries:
            yield ResourceRef(uri=rel, change_hint=hint)
        self.discovery_complete = True

    async def fetch(self, ref: ResourceRef, previous: PreviousState | None = None) -> FetchResult:
        p = self._resolve(ref.uri)
        now = datetime.now(timezone.utc)
        if p is None:
            return FetchResult(status="error", uri=ref.uri, error="path escapes source root", fetched_at=now)
        if not p.is_file():
            return FetchResult(status="gone", uri=ref.uri, fetched_at=now)
        hint = self._hint(p)
        if previous and previous.change_hint and previous.change_hint == hint:
            return FetchResult(status="not_modified", uri=ref.uri, change_hint=hint, fetched_at=now)
        size = p.stat().st_size
        if size > self.max_bytes:
            return FetchResult(
                status="error", uri=ref.uri, error=f"file exceeds {self.max_bytes} bytes", fetched_at=now
            )
        content = await asyncio.to_thread(p.read_bytes)
        ctype = mimetypes.guess_type(p.name)[0]
        return FetchResult(
            status="ok",
            uri=ref.uri,
            content=content,
            content_type=ctype,
            change_hint=hint,
            public_url=self.public_url(ref.uri),
            fetched_at=now,
        )
