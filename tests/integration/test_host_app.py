"""The unified demo host app (examples/fastapi-integration/app.py): one site that serves its own pages,
embeds the WebMind assistant for whoever is signed in, and protects internal content."""

import importlib.util
import re
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.conftest import JWT_SECRET

APP = Path(__file__).resolve().parents[2] / "examples" / "fastapi-integration" / "app.py"


@pytest.fixture
def host(monkeypatch, demo_dir):
    monkeypatch.setenv("WEBMIND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("WEBMIND_URL", "http://webmind.test:8080")
    spec = importlib.util.spec_from_file_location("host_app_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DOCS_DIR", (demo_dir / "generated" / "docs").resolve())
    monkeypatch.setattr(mod, "SITE_DIR", (demo_dir / "site").resolve())
    return mod, TestClient(mod.app)


def test_serves_the_real_pages_with_one_assistant_wired_to_the_persona(host):
    _mod, client = host
    html = client.get("/").text
    assert "<title>Lumen Valley Institute" in html and "Welcome to Lumen Valley Institute" in html
    assert len(re.findall(r"widget\.js", html)) == 1  # the site's own anonymous widget tag is replaced, not duplicated
    assert 'src="http://webmind.test:8080/v1/widget.js?v=' in html
    assert 'data-token-url="/api/webmind-token"' in html
    assert 'data-suggestions="Where can I find the eligibility requirements?|' in html
    assert 'id="wm-demo-bar"' in html and "Public visitor" in html and "Sign in" in html
    assert 'data-login-url="/login"' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html  # phones need it
    signed_in = client.get("/services.html", cookies={"user": "alice"}).text
    assert "alice" in signed_in and "Reviewer" in signed_in
    assert "Retrieve the records from my department between January and June." in signed_in
    assert client.get("/nope.html").status_code == 404
    assert client.get("/../README.md").status_code == 404
    assert client.get("/%2e%2e/%2e%2e/README.md").status_code in (400, 404)


def test_pages_on_disk_are_not_modified(host, demo_dir):
    _mod, client = host
    before = (demo_dir / "site" / "index.html").read_text(encoding="utf-8")
    client.get("/")
    assert (demo_dir / "site" / "index.html").read_text(encoding="utf-8") == before
    assert "demo-bar" not in before


def test_internal_pages_and_restricted_pdfs_follow_the_hosts_own_access_control(host):
    _mod, client = host
    internal = "/internal/staff-handbook.html"
    assert client.get(internal).status_code == 403
    assert client.get(internal, cookies={"user": "sam"}).status_code == 403  # student
    assert client.get(internal, cookies={"user": "evan"}).status_code == 200  # employee
    assert client.get("/docs/student-handbook-2026.pdf").status_code == 200
    research = "/docs/research-report-q1-2026.pdf"
    assert client.get(research).status_code == 403
    assert client.get(research, cookies={"user": "dana"}).status_code == 200  # research department
    assert client.get(research, cookies={"user": "sam"}).status_code == 403


def test_token_endpoint_signs_only_for_the_signed_in_persona(host):
    _mod, client = host
    assert client.get("/api/webmind-token").json() == {"token": None}
    assert client.get("/api/webmind-token", cookies={"user": "mallory"}).json() == {"token": None}  # unknown persona
    resp = client.get("/api/webmind-token", cookies={"user": "alice"})
    assert resp.headers["cache-control"] == "no-store"
    claims = jwt.decode(resp.json()["token"], JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "alice" and claims["roles"] == ["reviewer"]
    assert claims["attributes"]["review_to"] == "2026-06-30"
    assert claims["exp"] - claims["iat"] <= 300  # short-lived


def test_login_logout_switch_persona(host):
    _mod, client = host
    r = client.get("/login/ada", follow_redirects=False)
    assert r.status_code == 303 and "user=ada" in r.headers["set-cookie"]
    assert client.get("/login/nobody", follow_redirects=False).status_code == 404
    assert "user=" in client.get("/logout", follow_redirects=False).headers["set-cookie"]


def test_sign_in_page_and_safe_redirects(host):
    _mod, client = host
    page = client.get("/login?next=/admissions.html").text
    for name in ("evan", "dana", "alice", "ada", "sam"):
        assert f'href="/login/{name}?next=/admissions.html"' in page
    assert "Public visitor" in page and "Administrator" in page
    # signing in returns to the page you were on
    r = client.get("/login/alice?next=/admissions.html", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admissions.html"
    # never to another origin
    for evil in ("https://evil.example/", "//evil.example/x", "/" + chr(92) + "evil.example"):
        assert client.get("/login/alice", params={"next": evil}, follow_redirects=False).headers["location"] == "/"
    assert client.get("/logout?next=/about.html", follow_redirects=False).headers["location"] == "/about.html"
    # the persona menu on a page returns to that same page
    assert "/login/ada?next=/services.html" in client.get("/services.html").text
