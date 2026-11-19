"""Website crawler connector (read-only HTTP GET only).

- Discovery via sitemap.xml (and sitemap indexes) plus breadth-first link crawling
  restricted to the configured host.
- Honors robots.txt and ``<meta name="robots" content="noindex">``.
- Conditional requests (ETag / Last-Modified) avoid re-downloading unchanged pages; the
  stored outgoing links of unchanged pages keep discovery complete.
- Response size limits and a per-request delay protect both sides.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET  # noqa: S405 - only parses sitemap XML; no entity expansion in stdlib
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from webmind.core.config import WebsiteSourceConfig
from webmind.ingestion.connectors.base import FetchResult, PreviousState, ResourceRef, SourceConnector
from webmind.ingestion.parsers import detect_kind, extract_links

_SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|map|zip|gz|tar|mp4|mp3|wav|avi|mov|woff2?|ttf|eot|exe|dmg|iso)$",
    re.I,
)


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    netloc = p.netloc.lower()
    if (p.scheme == "http" and netloc.endswith(":80")) or (p.scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    return p._replace(scheme=p.scheme.lower(), netloc=netloc, path=path, params="").geturl()


class WebsiteConnector(SourceConnector):
    type = "website"

    def __init__(self, cfg: WebsiteSourceConfig, max_bytes: int, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(cfg.id)
        self.cfg = cfg
        self.max_bytes = max_bytes
        self.start = normalize_url(cfg.url)
        self.host = urlparse(self.start).netloc
        self._client = client or httpx.AsyncClient(
            timeout=cfg.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": cfg.user_agent, **cfg.headers},
        )
        self._include = [re.compile(p) for p in cfg.include]
        self._exclude = [re.compile(p) for p in cfg.exclude]
        self._robots: RobotFileParser | None = None
        self._results: dict[str, FetchResult] = {}
        self._last_request = 0.0

    # -- helpers ---------------------------------------------------------------
    def _in_scope(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.netloc != self.host:
            return False
        if _SKIP_EXT.search(p.path):
            return False
        if self._include and not any(r.search(url) for r in self._include):
            return False
        if any(r.search(url) for r in self._exclude):
            return False
        return not (self._robots is not None and not self._robots.can_fetch(self.cfg.user_agent, url))

    async def _throttle(self) -> None:
        if self.cfg.request_delay_seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        wait = self._last_request + self.cfg.request_delay_seconds - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = loop.time()

    async def _load_robots(self) -> None:
        if not self.cfg.respect_robots_txt:
            return
        rp = RobotFileParser()
        try:
            r = await self._client.get(urljoin(self.start, "/robots.txt"))
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        self._robots = rp

    async def _sitemap_urls(self) -> list[str]:
        candidates = [urljoin(self.start, "/sitemap.xml")]
        if self._robots is not None:
            candidates.extend(self._robots.site_maps() or [])
        seen: set[str] = set()
        urls: list[str] = []
        queue = deque((u, 0) for u in candidates)
        while queue and len(urls) < self.cfg.max_pages * 2:
            sm, depth = queue.popleft()
            if sm in seen or depth > 2:
                continue
            seen.add(sm)
            try:
                await self._throttle()
                r = await self._client.get(sm)
                if r.status_code != 200 or len(r.content) > self.max_bytes:
                    continue
                if b"<!DOCTYPE" in r.content[:512] or b"<!ENTITY" in r.content:
                    continue  # refuse DTDs/entities outright
                root = ET.fromstring(r.content)  # noqa: S314
            except (httpx.HTTPError, ET.ParseError):
                continue
            tag = root.tag.split("}")[-1]
            locs = [el.text.strip() for el in root.iter() if el.tag.split("}")[-1] == "loc" and el.text]
            if tag == "sitemapindex":
                queue.extend((u, depth + 1) for u in locs)
            else:
                urls.extend(locs)
        return urls

    async def _get(self, url: str, previous: PreviousState | None) -> FetchResult:
        headers: dict[str, str] = {}
        if previous:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            if previous.last_modified:
                headers["If-Modified-Since"] = previous.last_modified
        now = datetime.now(timezone.utc)
        await self._throttle()
        try:
            async with self._client.stream("GET", url, headers=headers) as r:
                final = normalize_url(str(r.url))
                if urlparse(final).netloc != self.host:
                    return FetchResult(status="error", uri=url, error="redirected off-site", fetched_at=now)
                if r.status_code == 304:
                    return FetchResult(
                        status="not_modified",
                        uri=url,
                        etag=previous.etag if previous else None,
                        last_modified=previous.last_modified if previous else None,
                        fetched_at=now,
                    )
                if r.status_code in (404, 410):
                    return FetchResult(status="gone", uri=url, fetched_at=now)
                if r.status_code >= 400:
                    return FetchResult(status="error", uri=url, error=f"HTTP {r.status_code}", fetched_at=now)
                body = bytearray()
                async for part in r.aiter_bytes():
                    body.extend(part)
                    if len(body) > self.max_bytes:
                        return FetchResult(status="error", uri=url, error="response too large", fetched_at=now)
                return FetchResult(
                    status="ok",
                    uri=final,
                    content=bytes(body),
                    content_type=r.headers.get("content-type"),
                    etag=r.headers.get("etag"),
                    last_modified=r.headers.get("last-modified"),
                    public_url=final,
                    fetched_at=now,
                )
        except httpx.HTTPError as exc:
            return FetchResult(status="error", uri=url, error=str(exc) or type(exc).__name__, fetched_at=now)

    # -- contract ----------------------------------------------------------------
    async def discover(self) -> AsyncIterator[ResourceRef]:
        self.discovery_complete = False
        self._results.clear()
        await self._load_robots()
        seeds = [self.start]
        if self.cfg.use_sitemap:
            seeds.extend(normalize_url(u) for u in await self._sitemap_urls())
        queue: deque[tuple[str, int]] = deque()
        queued: set[str] = set()
        for s in seeds:
            if s not in queued and self._in_scope(s):
                queued.add(s)
                queue.append((s, 0))
        yielded: set[str] = set()
        truncated = False
        while queue:
            if len(yielded) >= self.cfg.max_pages:
                truncated = True
                break
            url, depth = queue.popleft()
            prev = self.previous.get(url)
            result = await self._get(url, prev)
            uri = result.uri
            self._results[uri] = result
            links: list[str] = []
            if result.status == "ok" and result.content is not None:
                kind = detect_kind(result.content_type, uri)
                if kind is None:
                    continue
                if kind == "html":
                    links = extract_links(result.content)
            elif result.status == "not_modified" and prev is not None:
                links = prev.links
            elif result.status == "error":
                truncated = True  # we can't be sure what is missing
                if prev is None:
                    continue
            else:
                continue
            if uri not in yielded:
                yielded.add(uri)
                yield ResourceRef(uri=uri, etag=result.etag, last_modified=result.last_modified)
            if self.cfg.crawl and depth < self.cfg.max_depth:
                for href in links:
                    nxt = normalize_url(urljoin(uri, href))
                    if nxt not in queued and self._in_scope(nxt):
                        queued.add(nxt)
                        queue.append((nxt, depth + 1))
        self.discovery_complete = not truncated

    async def fetch(self, ref: ResourceRef, previous: PreviousState | None = None) -> FetchResult:
        cached = self._results.pop(ref.uri, None)
        if cached is not None:
            return cached
        url = normalize_url(ref.uri)
        if urlparse(url).netloc != self.host:
            return FetchResult(status="error", uri=ref.uri, error="out of scope")
        return await self._get(url, previous)

    async def confirm_missing(self, uri: str) -> bool:
        result = await self._get(normalize_url(uri), None)
        return result.status == "gone"

    async def aclose(self) -> None:
        await self._client.aclose()
