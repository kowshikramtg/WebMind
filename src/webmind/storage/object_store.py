"""Object storage adapters and content-addressable storage (CAS).

The CAS stores blobs under their SHA-256 fingerprint, compressed with Zstandard
(lossless). Identical content is stored exactly once.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import zstandard

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,511}$")


class ObjectStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


def validate_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key.split("/") or key.startswith("/"):
        raise ValueError(f"invalid object key: {key!r}")
    return key


class LocalObjectStore(ObjectStore):
    """Filesystem object store rooted at a directory. Keys cannot escape the root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"object key escapes store root: {key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StoredBlob:
    key: str
    sha256: str
    size: int
    stored_size: int
    deduplicated: bool


class ContentStore:
    """Content-addressable, Zstandard-compressed blob store on top of an ObjectStore."""

    def __init__(self, store: ObjectStore, level: int = 10) -> None:
        self.store = store
        self._cctx = zstandard.ZstdCompressor(level=level)
        self._dctx = zstandard.ZstdDecompressor()

    @staticmethod
    def key_for(digest: str) -> str:
        return f"cas/{digest[:2]}/{digest[2:4]}/{digest}.zst"

    def put(self, data: bytes) -> StoredBlob:
        digest = sha256_hex(data)
        key = self.key_for(digest)
        if self.store.exists(key):
            return StoredBlob(key, digest, len(data), -1, True)
        compressed = self._cctx.compress(data)
        self.store.put(key, compressed)
        return StoredBlob(key, digest, len(data), len(compressed), False)

    def get(self, digest: str) -> bytes:
        data = self._dctx.decompress(self.store.get(self.key_for(digest)))
        if sha256_hex(data) != digest:
            raise ValueError(f"content integrity check failed for {digest}")
        return data

    def exists(self, digest: str) -> bool:
        return self.store.exists(self.key_for(digest))

    def delete(self, digest: str) -> None:
        self.store.delete(self.key_for(digest))
