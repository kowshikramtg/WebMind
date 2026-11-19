"""Website crawler against an in-memory HTTP site (no network)."""

import httpx
import pytest

from webmind.core.config import WebsiteSourceConfig, config_from_dict
from webmind.ingestion.connectors.website import WebsiteConnector, normalize_url
from webmind.runtime import WebMindRuntime


class FakeSite:
    def __init__(self) -> None:
        self.pages = {
            "/": "<html><head><title>Home</title></head><body><main><h1>Home</h1><p>Welcome to the docs.</p>"
            '<a href="/guide">Guide</a> <a href="https://evil.example/x">offsite</a> <a href="/secret">s</a>'
            '<a href="/logo.png">logo</a> <a href="/redirect">r</a></main></body></html>',
            "/guide": '<html><head><title>Guide</title></head><body><h2 id="install">Installation</h2>'
            "<p>Install with pip install webmind.</p></body></html>",
            "/hidden": '<html><head><meta name="robots" content="noindex"></head><body><p>hidden</p></body></html>',
            "/secret": "<html><body><p>disallowed by robots</p></body></html>",
        }
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((path, dict(request.headers)))
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if path == "/sitemap.xml":
            xml = (
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://docs.example/guide</loc></url><url><loc>https://docs.example/hidden</loc></url>"
                "</urlset>"
            )
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})
        if path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/landing"})
        if path not in self.pages:
            return httpx.Response(404)
        etag = f'"{hash(self.pages[path]) & 0xFFFF}"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        return httpx.Response(200, text=self.pages[path], headers={"content-type": "text/html", "etag": etag})


def _src(**kw) -> WebsiteSourceConfig:
    return WebsiteSourceConfig(id="docs", type="website", url="https://docs.example/", request_delay_seconds=0, **kw)


def _client(site: FakeSite) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(site.handler), follow_redirects=True)


def test_normalize_url():
    assert normalize_url("HTTPS://Docs.Example:443/a#frag") == "https://docs.example/a"
    assert normalize_url("http://x.org") == "http://x.org/"


async def test_discovery_respects_scope_robots_and_noindex(tmp_path):
    site = FakeSite()
    cfg = config_from_dict(
        {"database": {"url": f"sqlite:///{tmp_path / 'wm.db'}"}, "object_store": {"path": str(tmp_path / "obj")}}
    )
    rt = WebMindRuntime(cfg)
    await rt.initialize()
    src = _src()
    conn = WebsiteConnector(src, 1_000_000, client=_client(site))
    report = await rt.sync_engine.sync_source(src, connector=conn)
    fetched = {p for p, _ in site.requests}
    assert "/secret" not in fetched  # robots.txt
    assert not any("evil" in p or p == "/logo.png" for p in fetched)
    assert report.new == 2 and report.skipped == 1  # home + guide; /hidden is noindex
    anon = rt.identity.anonymous()
    hits = await rt.retriever.search(anon, "how to install webmind with pip")
    assert hits and hits[0].anchor_url == "https://docs.example/guide#install"

    # Second sync uses conditional requests and stored links; nothing changes.
    site.requests.clear()
    conn2 = WebsiteConnector(src, 1_000_000, client=_client(site))
    report = await rt.sync_engine.sync_source(src, connector=conn2)
    assert report.not_modified == 2 and report.new == report.changed == 0
    assert any("if-none-match" in h for p, h in site.requests if p == "/guide")

    # A page removed from the site is confirmed gone and deleted from the index.
    del site.pages["/guide"]
    conn3 = WebsiteConnector(src, 1_000_000, client=_client(site))
    report = await rt.sync_engine.sync_source(src, connector=conn3)
    assert report.deleted == 1
    assert not await rt.retriever.search(anon, "install webmind pip")
    await rt.close()


async def test_max_pages_truncation_does_not_delete_unseen_pages(tmp_path):
    site = FakeSite()
    cfg = config_from_dict(
        {"database": {"url": f"sqlite:///{tmp_path / 'wm.db'}"}, "object_store": {"path": str(tmp_path / "obj")}}
    )
    rt = WebMindRuntime(cfg)
    await rt.initialize()
    await rt.sync_engine.sync_source(_src(), connector=WebsiteConnector(_src(), 1_000_000, client=_client(site)))
    limited = _src(max_pages=1)
    report = await rt.sync_engine.sync_source(
        limited, connector=WebsiteConnector(limited, 1_000_000, client=_client(site))
    )
    assert report.deleted == 0  # unseen pages are confirmed with a request, and they still exist
    await rt.close()


@pytest.mark.security
async def test_oversized_responses_are_refused():
    site = FakeSite()
    site.pages["/"] = "<html>" + "x" * 5000 + "</html>"
    conn = WebsiteConnector(_src(), 1000, client=_client(site))
    res = await conn.fetch(
        __import__("webmind.ingestion.connectors.base", fromlist=["ResourceRef"]).ResourceRef(
            uri="https://docs.example/"
        )
    )
    assert res.status == "error" and "too large" in (res.error or "")
