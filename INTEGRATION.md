# Integrating WebMind into your website or application

This guide walks you from "I have a website/app" to "my users can ask it questions",
with only as much infrastructure as you actually need.

- [1. Pick an integration path](#1-pick-an-integration-path)
- [2. Run WebMind (5 minutes)](#2-run-webmind-5-minutes)
- [3. Connect your content and data](#3-connect-your-content-and-data)
- [4. Public or signed-in users: identity and permissions](#4-public-or-signed-in-users-identity-and-permissions)
- [5. Add the assistant to your UI](#5-add-the-assistant-to-your-ui)
- [6. Call WebMind from your backend](#6-call-webmind-from-your-backend)
- [7. Keep the index in sync](#7-keep-the-index-in-sync)
- [8. Adding WebMind to your repository](#8-adding-webmind-to-your-repository)
- [9. Production checklist](#9-production-checklist)
- [10. Troubleshooting](#10-troubleshooting)

---

## 1. Pick an integration path

| Your stack | Recommended path | Section |
| --- | --- | --- |
| Static HTML, docs site, WordPress, PHP, any CMS | Drop-in `<script>` widget | [5.1](#51-drop-in-widget-any-website) |
| React / Next.js / Vite | `@webmind/react` components | [5.2](#52-react-and-nextjs) |
| Your own UI in any framework | REST API (`fetch`) or `WebMindClient` (JS) | [5.3](#53-your-own-ui-plain-javascript) |
| Python backend (FastAPI, Django, Flask) | In-process `webmind.WebMind` SDK — no extra server | [6.1](#61-python-in-process-no-separate-server) |
| Backend in any language | REST API + short-lived user tokens | [6.2](#62-rest-from-any-language) |

All paths share the same engine, configuration (`webmind.yaml`) and security model.

---

## 2. Run WebMind (5 minutes)

Requirements: Python 3.10+. No database server, GPU or LLM is required to start; the
defaults use SQLite, a built-in lexical embedder and extractive (quoted, cited) answers.

```bash
git clone https://github.com/kowshikramtg/WebMind.git
cd WebMind
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[pdf,xlsx,reports]"

mkdir my-assistant && cd my-assistant
webmind init . --source https://www.example.org      # or a local folder: --source ./site
webmind doctor                                        # validates config + connectivity
webmind index                                         # first ingestion
webmind serve                                         # http://127.0.0.1:8080/docs
```

`webmind init` writes `webmind.yaml`, a git-ignored `.env` with random secrets, and a
`data/` directory. Ask a question from the terminal to check everything works:

```bash
webmind ask "What does this site offer?"
```

Prefer containers? See [docs/deployment.md](docs/deployment.md) (`docker compose up -d`
with PostgreSQL + pgvector).

### Adding a language model (optional)

Without an LLM, answers are cited extracts. To get summarized, conversational answers,
set one provider in `webmind.yaml`:

```yaml
llm:
  provider: ollama                 # local: nothing leaves your servers
  model: llama3.1
  base_url: http://localhost:11434
# provider: openai                 # OpenAI or any OpenAI-compatible server (vLLM, LM Studio, ...)
# provider: anthropic              # Claude (pip install "webmind[anthropic]"), model: claude-opus-5-5
```

For semantic (meaning-based) search, also configure a neural embedding model and
re-embed (`webmind index --reembed`). See [docs/configuration.md](docs/configuration.md).

---

## 3. Connect your content and data

### 3.1 Website pages and documents (`sources`)

```yaml
sources:
  # Crawl a live site (sitemap.xml + links on the same host, robots.txt respected)
  - id: website
    type: website
    url: https://www.example.org
    max_pages: 2000
    exclude: ["/search", "\\?print="]
    access: { visibility: public }

  # Or index files directly (HTML, Markdown, TXT, PDF, DOCX)
  - id: handbooks
    type: filesystem
    path: ./content/handbooks
    base_url: https://intranet.example.org/handbooks   # turns file paths into citation links
    access: { visibility: restricted, roles: [employee] }
```

Mixed public/private areas get per-path rules. The first matching rule wins:

```yaml
    path_rules:
      - pattern: "^https://www.example.org/staff/"
        access: { visibility: restricted, roles: [employee, admin] }
      - pattern: "/research/"
        access: { visibility: restricted, attributes: { department: [research] } }
```

Tips for better navigation answers:

- Give important headings an `id` (`<h2 id="eligibility">`). WebMind links straight to
  that section.
- Mark boilerplate with `data-webmind-ignore` (cookie banners, related-article blocks).
  `<nav>`, `<footer>`, `<aside>`, scripts and forms are skipped automatically.
- Pages with `<meta name="robots" content="noindex">` are never indexed.

### 3.2 Structured data (`datasets`)

Datasets let users ask for records, statistics, charts and exports in plain language.
WebMind never runs model-written SQL. It compiles validated query specs against the
columns you list here, on a read-only connection.

```yaml
datasets:
  - id: projects
    description: Projects with department, status, creation date and budget
    synonyms: [project, records]
    connection: ${PROJECTS_DB_URL}   # use a READ-ONLY database role
    table: projects
    date_column: created_at
    columns:
      - { name: name, type: string }
      - { name: department, type: string, groupable: true, values: [engineering, research, finance] }
      - { name: status, type: string, groupable: true, values: [planned, active, completed] }
      - { name: created_at, type: date }
      - { name: budget, type: number, aggregatable: true }
    access: { visibility: restricted, roles: [employee, manager] }
    row_filters:          # users only see their own department unless they are managers
      - { column: department, equals_attribute: department, unless_roles: [manager] }
    max_rows: 1000
```

Only listed columns are ever queried. `values` help the assistant understand phrases
like "engineering projects". Supported backends: SQLite, PostgreSQL, MySQL/MariaDB.

---

## 4. Public or signed-in users: identity and permissions

### 4.1 Public sites

With the default `security.authentication.mode: public`, every visitor is an anonymous
principal with the `public` role. They see only content whose access rule is
`visibility: public`. There's nothing else to do.

### 4.2 Signed-in users (host-application identity)

WebMind does **not** manage users or passwords. Your application already knows who
is signed in, so it vouches for them with a short-lived signed token:

```
Browser ──(your session cookie)──▶ Your backend ──signs──▶ token (JWT, ≤ 1 h)
Browser ──(Authorization: Bearer token)──────────────────▶ WebMind ──verifies──▶ principal
```

Enable it in `webmind.yaml`:

```yaml
security:
  authentication:
    mode: host_application
    allow_anonymous: true          # still serve public content to visitors without a token
    jwt:
      algorithms: [HS256]          # or RS256/ES256 with public_key: ./keys/webmind.pub
      secret: ${WEBMIND_JWT_SECRET}
      # issuer: my-app             # optional, recommended
      # audience: webmind          # optional, recommended
  roles:
    public:   { capabilities: [chat, search, navigate, "documents:read"] }
    employee: { inherits: [public], capabilities: ["records:read", analytics] }
    manager:  { inherits: [employee], capabilities: [export, reports] }
```

**Token contract**

| Claim | Required | Meaning |
| --- | --- | --- |
| `sub` | yes | Stable user id in your system |
| `iat`, `exp` | yes | Issued-at / expiry; lifetime ≤ `max_token_lifetime_seconds` (default 3600) |
| `roles` | no | List of role names from `security.roles` (unknown roles grant nothing) |
| `attributes` | no | Flat object used by ABAC rules, e.g. `{"department": "engineering"}` |
| `tenant` | no | Tenant id for multi-tenant deployments (default `app.default_tenant`) |
| `iss`, `aud` | if configured | Checked when `issuer` / `audience` are set |

Invalid, expired or tampered tokens get `401`. They never fall back to anonymous access.

**Signing tokens in your backend.** Never sign tokens in the browser: the secret must
stay on your server.

Python (`pip install ./sdk/python`, or use PyJWT directly):

```python
from webmind_client import sign_user_token

token = sign_user_token(os.environ["WEBMIND_JWT_SECRET"], user_id=user.id,
                        roles=user.roles, attributes={"department": user.department},
                        ttl_seconds=600)
```

Node.js (`npm install jose`):

```js
import { SignJWT } from "jose";

const secret = new TextEncoder().encode(process.env.WEBMIND_JWT_SECRET);
export async function webmindToken(user) {
  return new SignJWT({ roles: user.roles, attributes: { department: user.department } })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(String(user.id))
    .setIssuedAt()
    .setExpirationTime("10m")
    .sign(secret);
}
```

PHP (`composer require firebase/php-jwt`):

```php
use Firebase\JWT\JWT;

function webmind_token(array $user): string {
    $now = time();
    return JWT::encode([
        'sub' => (string) $user['id'],
        'roles' => $user['roles'],
        'attributes' => ['department' => $user['department']],
        'iat' => $now,
        'exp' => $now + 600,
    ], getenv('WEBMIND_JWT_SECRET'), 'HS256');
}
```

Expose it to your own front end as an endpoint that returns `{"token": "..."}` for the
signed-in user, or `{"token": null}` for visitors. The endpoint must use your normal session
authentication.

### 4.3 What permissions control

| Mechanism | Configured in | Example |
| --- | --- | --- |
| Capabilities (RBAC) | `security.roles` | only `manager` may `export` |
| Content visibility | `sources[].access`, `path_rules` | `/staff/` pages for employees only |
| Attribute rules (ABAC) | `access.attributes` | research PDFs for `department: research` |
| Row-level filters | `datasets[].row_filters` | own department's rows only |
| Date windows | `datasets[].date_window` | reviewers see only their review period |
| Tenant isolation | token `tenant` claim | tenants never see each other's data |

All of these are enforced *before* anything reaches a language model, and on every
request, including follow-up questions and exports. Writes to your sources are not
possible: WebMind is read-only by construction.

---

## 5. Add the assistant to your UI

### 5.1 Drop-in widget (any website)

One script tag, with no build step and no dependencies. It works on static sites, WordPress, PHP
templates, Django/Rails views and so on:

```html
<script src="https://assistant.example.org/v1/widget.js"
        data-endpoint="https://assistant.example.org"
        data-token-url="/api/webmind-token"
        data-title="Ask us anything"
        defer></script>
```

- Omit `data-token-url` on purely public sites.
- `data-token-url` is fetched with the visitor's cookies (`credentials: "include"`), so it
  should be a same-origin endpoint of *your* site, as in section 4.2.
- Add your site's origin to `server.cors_origins` (e.g. `["https://www.example.org"]`),
  or proxy WebMind under your own domain (recommended, see 5.2).

**WordPress.** In your theme's `functions.php`:

```php
add_action('wp_enqueue_scripts', function () {
    wp_enqueue_script('webmind', 'https://assistant.example.org/v1/widget.js', [], null, true);
});
add_filter('script_loader_tag', function ($tag, $handle) {
    if ($handle !== 'webmind') return $tag;
    return str_replace('<script ', '<script data-endpoint="https://assistant.example.org" data-token-url="/wp-json/webmind/v1/token" ', $tag);
}, 10, 2);
add_action('rest_api_init', function () {
    register_rest_route('webmind/v1', '/token', ['methods' => 'GET', 'permission_callback' => '__return_true',
        'callback' => function () {
            $u = wp_get_current_user();
            if (!$u->exists()) return ['token' => null];
            return ['token' => webmind_token(['id' => $u->ID, 'roles' => $u->roles, 'department' => get_user_meta($u->ID, 'department', true)])];
        }]);
});
```

(`webmind_token` is the PHP function from section 4.2. `wp_get_current_user()` works in REST callbacks when the request carries the WordPress nonce or cookie auth. Adapt it to your auth setup.)

### 5.2 React and Next.js

```bash
npm install /path/to/WebMind/sdk/react       # until published on npm
```

```tsx
import { WebMindProvider, WebMindChat } from "@webmind/react";

const getSessionToken = async () => {
  const r = await fetch("/api/webmind-token", { credentials: "include" });
  return r.ok ? (await r.json()).token : null;
};

export default function HelpPanel() {
  return (
    <WebMindProvider endpoint="/webmind-api" getAccessToken={getSessionToken}>
      <WebMindChat title="Ask the handbook" />
    </WebMindProvider>
  );
}
```

**Next.js:** proxy WebMind under your own origin (no CORS, one cookie domain), and add a
token route:

```js
// next.config.js
module.exports = {
  async rewrites() {
    return [{ source: "/webmind-api/:path*", destination: `${process.env.WEBMIND_URL}/:path*` }];
  },
};
```

```ts
// app/api/webmind-token/route.ts
import { NextResponse } from "next/server";
import { webmindToken } from "@/lib/webmind-token";   // the jose snippet from section 4.2
import { getCurrentUser } from "@/lib/auth";          // your existing auth

export async function GET() {
  const user = await getCurrentUser();
  return NextResponse.json({ token: user ? await webmindToken(user) : null });
}
```

Want a custom UI? Use the `useWebMind()` hook (`turns`, `ask`, `pending`, `reset`) and
render `response.answer`, `citations`, `navigation`, `table`, `chart` (with `<ChartView>`)
and `export` yourself.

### 5.3 Your own UI (plain JavaScript)

```js
import { WebMindClient } from "@webmind/react/client";   // framework-free; or use fetch directly

const wm = new WebMindClient({ endpoint: "/webmind-api", getAccessToken: getSessionToken });
const res = await wm.chat("Where can I find the eligibility requirements?");
// res.answer, res.citations[], res.navigation[], res.table, res.chart, res.export
for await (const ev of wm.stream("Summarize the admissions page")) {
  if (ev.type === "token") appendText(ev.text);
  if (ev.type === "final") showCitations(ev.response.citations);
}
```

Always render answers and citation text as **text**, not HTML, and only allow `http(s)`
links. The bundled widget and React components already do both.

---

## 6. Call WebMind from your backend

### 6.1 Python in-process (no separate server)

```python
from webmind import WebMind

assistant = WebMind(config="webmind.yaml")
await assistant.initialize()

# You have already authenticated the user; pass their roles/attributes.
res = await assistant.ask(
    "Retrieve the records from my department between January and June",
    user_id=user.id, roles=user.roles, attributes={"department": user.department},
)
print(res.answer, res.table, res.citations)
await assistant.sync()          # e.g. from a scheduled job
```

The in-process SDK trusts the identity you pass, so only call it with users your app has
authenticated. A complete FastAPI host app is in
[examples/fastapi-integration/app.py](examples/fastapi-integration/app.py).

### 6.2 REST from any language

Interactive OpenAPI docs: `http://<webmind>/docs`. Main endpoints (see [docs/api.md](docs/api.md)):

| Method & path | Purpose | Capability |
| --- | --- | --- |
| `POST /v1/chat` | Ask a question (`{"message", "conversation_id"?}`) | `chat` |
| `POST /v1/chat/stream` | Same, as server-sent events | `chat` |
| `POST /v1/search` | Raw permission-filtered retrieval | `search` + `documents:read` |
| `POST /v1/analytics/query` | Structured records / aggregates | `records:read` / `analytics` |
| `POST /v1/exports` → `GET /v1/exports/{id}/download` | CSV, JSON, XLSX, PDF | `export` |
| `GET /v1/resources` | Resources the caller can see | `documents:read` |
| `GET /v1/sync/status` | Sync state | `sync:status` |
| `GET /v1/health`, `/v1/health/ready` | Liveness / readiness | — |

```bash
curl -s https://assistant.example.org/v1/chat \
  -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "Compare monthly performance across departments"}'
```

Keep the `conversation_id` from each response and send it back to keep follow-ups like
"export these into Excel" in context.

---

## 7. Keep the index in sync

| Method | When to use | How |
| --- | --- | --- |
| Built-in scheduler | Simple sites | `synchronization.scheduler: true` + `sources[].sync.interval_minutes: 60` |
| Cron / CI job | You already run cron | `webmind sync` (incremental; unchanged pages cost a conditional request) |
| Webhooks | Your CMS knows when content changes | `POST /v1/webhooks/{source_id}`, signed |
| Admin API | Ops tooling | `POST /v1/admin/sync` with `X-WebMind-Admin-Key` |

Webhook example: set `webhook_secret: ${CMS_WEBHOOK_SECRET}` on the source, then sign the raw
body with HMAC-SHA256:

```python
import hashlib, hmac, json, httpx

body = json.dumps({"uris": ["https://www.example.org/admissions"]}).encode()   # omit "uris" for a full incremental sync
sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
httpx.post("https://assistant.example.org/v1/webhooks/website", content=body,
           headers={"X-WebMind-Signature": sig, "Content-Type": "application/json"})
```

Changed pages are re-indexed, and deleted pages leave retrieval immediately. Every answer
carries freshness metadata (`last_synced_at`), and indexed content is never presented as
live data.

---

## 8. Adding WebMind to your repository

A layout that works well alongside an existing app:

```
your-app/
├── webmind/
│   ├── webmind.yaml          # committed: sources, datasets, roles (no secrets)
│   ├── .env                  # NOT committed: WEBMIND_JWT_SECRET, WEBMIND_ADMIN_API_KEY, DB URLs
│   └── data/                 # NOT committed: index, object store, exports
├── docker-compose.yml        # add a `webmind` service (see below)
└── .gitignore                # add: webmind/.env, webmind/data/
```

Docker Compose service next to your app:

```yaml
  webmind:
    image: webmind:0.1.0                     # built from this repository's Dockerfile
    env_file: ./webmind/.env
    volumes:
      - ./webmind/webmind.yaml:/app/config/webmind.yaml:ro
      - ./public:/app/content:ro             # your site's files, read-only
      - webmind-data:/app/data
    ports: ["127.0.0.1:8080:8080"]
```

In CI you can validate the config on every change with `webmind doctor --config webmind/webmind.yaml`.
To catch retrieval regressions, keep a small question set and run `webmind eval questions.jsonl`
(format in [docs/evaluation.md](docs/evaluation.md)).

---

## 9. Production checklist

- [ ] `WEBMIND_JWT_SECRET` is random (≥ 32 chars) and lives only in your backend and WebMind.
- [ ] Tokens are short-lived (5–15 min) and signed only for authenticated users.
- [ ] Dataset `connection`s use a **read-only database role** (defense in depth on top of WebMind's read-only sessions).
- [ ] `server.cors_origins` lists your exact origins, or WebMind is proxied under your domain.
- [ ] WebMind runs behind TLS (reverse proxy). Use `webmind serve --proxy-headers` with `FORWARDED_ALLOW_IPS` set to your proxy.
- [ ] `server.admin_api_key` is set (or left unset to disable admin endpoints).
- [ ] Back up the metadata database and `data/objects` (see [docs/deployment.md](docs/deployment.md)).
- [ ] `webmind doctor` reports no problems. Ask a few restricted questions as anonymous and as each role.
- [ ] Review `GET /v1/admin/audit?decision=deny` periodically.

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| "I couldn't find information…" for content you know exists | `webmind sync`, then check `webmind connectors list`; confirm the page's access rule admits the user; check `noindex`. |
| `401 invalid token` | Clock skew (> `leeway_seconds`), expired token, wrong secret/algorithm, missing `iat`/`exp`/`sub`, lifetime above `max_token_lifetime_seconds`. |
| `403` on analytics/exports | The role lacks the capability (`records:read`, `analytics`, `export`) or the dataset `access` rule excludes the user. |
| Records list is empty for a user | A `row_filters` attribute is missing from the token. Missing attributes deny by default. |
| Browser CORS error | Add the site origin to `server.cors_origins`, or proxy WebMind same-origin. |
| Answers quote passages instead of summarizing | No LLM configured (`llm.provider: none`). See section 2. |
| `embedding model changed` warning | You changed `embeddings`. Run `webmind index --reembed`. |
