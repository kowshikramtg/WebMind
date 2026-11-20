"""Example HOST application: one site that serves its own pages AND embeds WebMind, with signed-token identity.

This is the single system to run for the Lumen Valley demo. It:

  * serves the Lumen Valley website (examples/basic-website/site, unchanged on disk) and its PDFs,
  * protects the internal pages the way a real host would (staff only),
  * embeds the WebMind assistant on every page, wired to whoever is signed in,
  * signs a short-lived WebMind token for the signed-in persona (the host owns authentication;
    WebMind verifies the token and enforces authorization).

Run (from the repository root; see examples/basic-website/DEMO.md):

    export WEBMIND_JWT_SECRET=<same value the WebMind server uses>
    webmind serve -c examples/basic-website/webmind-ollama.yaml     # terminal 1: http://localhost:8080
    python examples/fastapi-integration/app.py                      # terminal 2: http://localhost:8000

The cookie "login" is a demo toy that switches personas. Use your real authentication in production.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from webmind import WebMind
from webmind.security.identity import issue_token  # or: from webmind_client import sign_user_token

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "basic-website"
SITE_DIR = (DEMO / "site").resolve()
DOCS_DIR = (DEMO / "generated" / "docs").resolve()
WEBMIND_URL = os.environ.get("WEBMIND_URL", "http://localhost:8080").rstrip("/")
JWT_SECRET = os.environ["WEBMIND_JWT_SECRET"]
DEMO_CONFIG = Path(os.environ.get("WEBMIND_DEMO_CONFIG", DEMO / "webmind.yaml"))
BOOT = int(time.time())  # cache-busts the widget script after a restart

STAFF = {"employee", "reviewer", "manager", "admin"}

# Your real user store. Roles/attributes must match security.roles / dataset rules in webmind.yaml.
USERS: dict[str, dict] = {
    "evan": {
        "title": "Employee, Engineering",
        "desc": "Engineering records and budgets, internal pages. Cannot export.",
        "roles": ["employee"],
        "attributes": {"department": "engineering"},
        "try": [
            "What is the average budget per department?",
            "Show me the engineering projects",
            "What is the leave policy for staff?",
        ],
    },
    "dana": {
        "title": "Employee, Research",
        "desc": "Research records and revenue, the research report. A different department from Evan.",
        "roles": ["employee"],
        "attributes": {"department": "research"},
        "try": [
            "Show me the engineering projects",
            "Show monthly revenue as a line chart",
            "How many patent applications did the research department file?",
        ],
    },
    "alice": {
        "title": "Reviewer, Engineering, Jan-Jun 2026",
        "desc": "Engineering records inside her review window; can export to Excel, CSV, PDF, JSON.",
        "roles": ["reviewer"],
        "attributes": {"department": "engineering", "review_from": "2026-01-01", "review_to": "2026-06-30"},
        "try": [
            "Retrieve the records from my department between January and June.",
            "Retrieve the records from my department between July and November.",
            "Export these results into Excel.",
        ],
    },
    "ada": {
        "title": "Administrator",
        "desc": "Everything: all departments, all dates, budgets and revenue.",
        "roles": ["admin"],
        "attributes": {},
        "try": [
            "What is the average budget per department?",
            "Show monthly revenue as a line chart",
            "Export the project records into Excel",
        ],
    },
    "sam": {
        "title": "Student",
        "desc": "Public information only; no internal pages or raw records.",
        "roles": ["student"],
        "attributes": {},
        "try": ["Show all project records", "Generate a bar chart showing the number of projects by department."],
    },
}
PUBLIC_TRY = [
    "Where can I find the eligibility requirements?",
    "Generate a bar chart showing the number of projects by department.",
    "Show monthly metrics as a line chart for the available date range.",
]

assistant = WebMind(config=DEMO_CONFIG)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await assistant.initialize()
    yield
    await assistant.close()


app = FastAPI(
    title="Lumen Valley (host app with WebMind)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


# ------------------------------------------------------------------ identity (demo)


def safe_next(target: str | None) -> str:
    """Only same-site relative paths: never redirect to another origin."""
    return target if target and target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str | None = None, user: str | None = Cookie(default=None)) -> str:
    """Demo sign-in page. A real host would authenticate here; WebMind only ever sees the signed token."""
    dest = quote(safe_next(next), safe="/")
    cards = "".join(
        f'<a class="card{" on" if n == user else ""}" href="/login/{n}?next={dest}"><b>{n}</b>'
        f"<span>{escape(d['title'])}</span><small>{escape(d['desc'])}</small></a>"
        for n, d in USERS.items()
    )
    anon = (
        f'<a class="card{"" if user in USERS else " on"}" href="/logout?next={dest}"><b>Public visitor</b>'
        "<span>Not signed in</span><small>Public pages and public statistics only.</small></a>"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in - Lumen Valley</title>
<style>body{{margin:0;background:#f2f2f7;color:#1c1c1e;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}main{{max-width:560px;margin:7vh auto;padding:0 18px}}
h1{{font-size:28px;letter-spacing:-.02em;margin:0 0 4px}}p{{color:#636366;margin:0 0 22px}}.card{{display:block;background:#fff;border:1px solid rgba(60,60,67,.14);border-radius:16px;padding:14px 16px;margin-bottom:10px;color:inherit;text-decoration:none;transition:transform .15s,box-shadow .15s}}
.card:hover{{transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,0,0,.08)}}.card.on{{border-color:#4353d6;box-shadow:0 0 0 3px rgba(67,83,214,.16)}}.card b{{font-size:17px}}.card span{{display:block;color:#4353d6;font-weight:600;font-size:13px;margin-top:2px}}.card small{{display:block;color:#636366;margin-top:4px;font-size:13px}}
small.n{{color:#8e8e93}}@media (prefers-color-scheme:dark){{body{{background:#000;color:#f5f5f7}}.card{{background:#1c1c1e;border-color:rgba(255,255,255,.12)}}p,.card small{{color:#aeaeb2}}.card span{{color:#8f9bff}}}}</style></head>
<body><main><h1>Sign in</h1><p>Demo accounts. The assistant answers according to the account you choose: what it may show, which departments and dates, and whether you can export.</p>{cards}{anon}<small class="n">Demo sign-in only: a real site authenticates here and signs the token for the user it has verified.</small></main></body></html>"""


@app.get("/login/{name}")
def login(name: str, next: str | None = None) -> Response:
    if name not in USERS:
        raise HTTPException(404)
    response = Response(status_code=303, headers={"Location": safe_next(next)})
    response.set_cookie("user", name, httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout(next: str | None = None) -> Response:
    response = Response(status_code=303, headers={"Location": safe_next(next)})
    response.delete_cookie("user")
    return response


@app.get("/api/webmind-token")
def webmind_token(user: str | None = Cookie(default=None)) -> Response:
    """Sign a short-lived WebMind token for the CURRENT user only (anonymous visitors get none)."""
    if user not in USERS:
        body = '{"token":null}'
    else:
        u = USERS[user]
        token = issue_token(JWT_SECRET, user_id=user, roles=u["roles"], attributes=u["attributes"], ttl_seconds=300)
        body = f'{{"token":"{token}"}}'
    return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/whoami")
def whoami(user: str | None = Cookie(default=None)) -> dict:
    """Show what this host app vouches for (demo aid)."""
    u = USERS.get(user or "")
    if not u:
        return {"user": None, "roles": ["public"], "attributes": {}}
    return {"user": user, "roles": u["roles"], "attributes": u["attributes"], "token_ttl_seconds": 300}


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None


@app.post("/api/ask")
async def ask(body: AskBody, user: str | None = Cookie(default=None)) -> dict:
    """In-process SDK pattern: this backend vouches for the user's identity."""
    u = USERS.get(user or "")
    res = await assistant.ask(
        body.question,
        user_id=user if u else None,
        roles=u["roles"] if u else None,
        attributes=u["attributes"] if u else None,
        conversation_id=body.conversation_id,
    )
    return res.model_dump(mode="json")


# ------------------------------------------------------------------ the website itself


def can_view(rel: str, name: str | None) -> bool:
    """The host's own access control for its pages (mirrors the rules configured in WebMind)."""
    u = USERS.get(name or "")
    if rel.startswith("internal/"):
        return bool(u and STAFF & set(u["roles"]))
    if rel.startswith("docs/research-report"):
        return bool(u and u["attributes"].get("department") in ("research", "engineering"))
    return True


def persona_bar(name: str | None, path: str = "/") -> str:
    cur = USERS.get(name or "")
    nxt = quote(path, safe="/")
    label = (
        f"Signed in as <b>{escape(name or '')}</b> &middot; {escape(cur['title'])}"
        if cur
        else "Public visitor &middot; <b>Sign in</b>"
    )
    items = "".join(
        f'<a href="/login/{u}?next={nxt}"{" class=on" if u == name else ""}><b>{u}</b><small>{escape(d["title"])}</small></a>'
        for u, d in USERS.items()
    )
    anon = (
        f'<a href="/logout?next={nxt}"'
        + ("" if cur else " class=on")
        + "><b>Public visitor</b><small>not signed in</small></a>"
    )
    return f"""<div id="wm-demo-bar"><details><summary><i></i>{label}</summary><div class="m"><div class="h">Sign in as</div>{anon}{items}<a href="/login?next={nxt}"><small>All accounts and what they can do &rsaquo;</small></a></div></details></div>
<style>
#wm-demo-bar{{position:fixed;left:16px;bottom:16px;z-index:2147482000;font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1c1c1e}}
#wm-demo-bar summary{{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,.86);-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);border:1px solid rgba(60,60,67,.16);box-shadow:0 6px 20px rgba(0,0,0,.12);font-weight:600}}
#wm-demo-bar summary::-webkit-details-marker{{display:none}}
#wm-demo-bar summary i{{width:8px;height:8px;border-radius:50%;background:#34c759}}
#wm-demo-bar .m{{position:absolute;bottom:calc(100% + 8px);left:0;min-width:250px;padding:6px;border-radius:16px;background:rgba(255,255,255,.92);-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);border:1px solid rgba(60,60,67,.16);box-shadow:0 14px 40px rgba(0,0,0,.2)}}
#wm-demo-bar .h{{padding:6px 10px 4px;font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:#8e8e93}}
#wm-demo-bar a{{display:flex;flex-direction:column;padding:7px 10px;border-radius:10px;color:inherit;text-decoration:none}}
#wm-demo-bar a:hover{{background:rgba(67,83,214,.1)}}#wm-demo-bar a.on{{background:rgba(67,83,214,.14)}}
#wm-demo-bar a small{{color:#6c6c70}}
@media (prefers-color-scheme:dark){{#wm-demo-bar{{color:#f5f5f7}}#wm-demo-bar summary,#wm-demo-bar .m{{background:rgba(44,44,46,.88);border-color:rgba(255,255,255,.14)}}#wm-demo-bar a small{{color:#aeaeb2}}}}
@media (max-width:520px){{#wm-demo-bar{{left:10px;bottom:10px}}}}
</style>"""


def widget_tag(name: str | None) -> str:
    tries = USERS[name]["try"] if name in USERS else PUBLIC_TRY
    return (
        f'<script src="{WEBMIND_URL}/v1/widget.js?v={BOOT}" data-endpoint="{WEBMIND_URL}" '
        f'data-token-url="/api/webmind-token" data-login-url="/login" data-title="Ask Lumen Valley" '
        f'data-suggestions="{escape("|".join(tries), quote=True)}" defer></script>'
    )


def inject(page: str, name: str | None, path: str = "/") -> str:
    """Wire the WebMind widget to the signed-in persona without changing the page files on disk."""
    import re

    tag = widget_tag(name)
    existing = re.compile(r"<script[^>]*widget\.js[^>]*>\s*</script>", re.I)
    page = existing.sub("", page)  # the site may already embed an anonymous widget; ours replaces it
    if not re.search(r"<meta[^>]+name=[\"']viewport", page, re.I):
        # Phones lay a page out at ~980px unless it declares a viewport, which also shrinks the assistant.
        # A host page should declare one; we add it at serve time (the files on disk are not modified).
        viewport = '<meta name="viewport" content="width=device-width, initial-scale=1">'
        page, n = re.subn(r"(<head[^>]*>)", lambda m: m.group(1) + viewport, page, count=1, flags=re.I)
        if not n:
            page = viewport + page
    extra = persona_bar(name, path) + tag
    return (
        re.sub(r"</body>", lambda _m: extra + "</body>", page, count=1, flags=re.I)
        if "</body>" in page.lower()
        else page + extra
    )


@app.get("/{path:path}")
def site(path: str, user: str | None = Cookie(default=None)) -> Response:
    rel = path or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    base, sub = (DOCS_DIR, rel[5:]) if rel.startswith("docs/") else (SITE_DIR, rel)
    target = (base / sub).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404)
    if not can_view(rel, user):
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Staff only</title>"
            "<body style='font:16px system-ui;max-width:560px;margin:15vh auto;padding:0 20px'>"
            "<h1>Staff only</h1><p>This page is not available to your account. Use the persona menu "
            "(bottom left) to browse as a staff member.</p><p><a href='/'>Back to the site</a></p></body>",
            status_code=403,
        )
    if target.suffix.lower() in (".html", ".htm"):
        return HTMLResponse(
            inject(target.read_text(encoding="utf-8"), user, "/" + rel), headers={"Cache-Control": "no-store"}
        )
    return FileResponse(target, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import socket
    import sys

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            sys.exit(
                f"\nPort {port} is already in use by another program (most likely the old `python -m http.server {port}`\n"
                "or a previous copy of this app). That program does not know who is signed in, so the assistant would treat\n"
                "everyone as a public visitor. Stop it and run this again, or choose another port with PORT=<number>.\n"
            )
    uvicorn.run(app, host="127.0.0.1", port=port)
