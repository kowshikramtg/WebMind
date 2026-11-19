"""Example HOST application integrating WebMind (FastAPI).

Shows the two supported integration patterns:

1. Browser → WebMind server, with identity from this app:
   ``GET /api/webmind-token`` signs a short-lived token for the logged-in user; the
   embedded widget (or @webmind/react) sends it to WebMind as a Bearer token.

2. Server-side, in-process: ``POST /api/ask`` calls the WebMind Python SDK directly
   (no separate WebMind server needed).

Run (from the repository root, after `python examples/basic-website/setup_demo.py`):

    export WEBMIND_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    webmind index --config examples/basic-website/webmind.yaml
    webmind serve --config examples/basic-website/webmind.yaml          # :8080
    python examples/fastapi-integration/app.py                          # :8000

Then open http://localhost:8000 and log in as one of the demo users.

The "login" here is a toy cookie for demonstration only — use your real auth system.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from webmind import WebMind
from webmind.security.identity import issue_token  # or: from webmind_client import sign_user_token

ROOT = Path(__file__).resolve().parents[2]
WEBMIND_URL = os.environ.get("WEBMIND_URL", "http://localhost:8080")
JWT_SECRET = os.environ["WEBMIND_JWT_SECRET"]

# Your real user store. Roles/attributes must match security.roles / dataset rules in webmind.yaml.
USERS = {
    "alice": {
        "roles": ["reviewer"],
        "attributes": {"department": "engineering", "review_from": "2026-01-01", "review_to": "2026-06-30"},
    },
    "maria": {"roles": ["manager"], "attributes": {}},
    "sam": {"roles": ["student"], "attributes": {}},
}

assistant = WebMind(config=ROOT / "examples" / "basic-website" / "webmind.yaml")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await assistant.initialize()
    yield
    await assistant.close()


app = FastAPI(title="Host app with WebMind", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def home(user: str | None = Cookie(default=None)) -> str:
    who = (
        f"Logged in as <b>{user}</b> · <a href='/logout'>log out</a>"
        if user in USERS
        else ("Not logged in · " + " ".join(f"<a href='/login/{u}'>log in as {u}</a>" for u in USERS))
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Host app</title></head>
<body style="font-family:system-ui;max-width:720px;margin:40px auto">
<h1>Lumen Valley intranet (demo host app)</h1><p>{who}</p>
<p>Try: “Retrieve the records from my department between January and June”, then “Export these records into Excel”.</p>
<script src="{WEBMIND_URL}/v1/widget.js" data-endpoint="{WEBMIND_URL}" data-token-url="/api/webmind-token" defer></script>
</body></html>"""


@app.get("/login/{name}")
def login(name: str, response: Response) -> Response:
    if name not in USERS:
        raise HTTPException(404)
    response = Response(status_code=303, headers={"Location": "/"})
    response.set_cookie("user", name, httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout() -> Response:
    response = Response(status_code=303, headers={"Location": "/"})
    response.delete_cookie("user")
    return response


@app.get("/api/webmind-token")
def webmind_token(user: str | None = Cookie(default=None)) -> dict:
    """Pattern 1: sign a short-lived WebMind token for the CURRENT user only."""
    if user not in USERS:
        return {"token": None}  # anonymous: WebMind will only use public content
    u = USERS[user]
    return {
        "token": issue_token(JWT_SECRET, user_id=user, roles=u["roles"], attributes=u["attributes"], ttl_seconds=300)
    }


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None


@app.post("/api/ask")
async def ask(body: AskBody, user: str | None = Cookie(default=None)) -> dict:
    """Pattern 2: in-process SDK call; this backend vouches for the user's identity."""
    u = USERS.get(user or "")
    res = await assistant.ask(
        body.question,
        user_id=user if u else None,
        roles=u["roles"] if u else None,
        attributes=u["attributes"] if u else None,
        conversation_id=body.conversation_id,
    )
    return res.model_dump(mode="json")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
