# Lumen Valley demo: one site, public and signed-in

There is **one system to run**: the Lumen Valley website served by a small host app that embeds the WebMind
assistant. The same pages work for anonymous visitors and for signed-in personas. The host app owns
authentication (it signs a short-lived token for whoever is signed in); WebMind verifies it and enforces
authorization.

```
 browser ── http://localhost:8000 ──▶ host app (examples/fastapi-integration/app.py)
                                      ├─ serves the Lumen Valley pages + PDFs (files unchanged on disk)
                                      ├─ protects internal pages (staff only)
                                      ├─ injects the assistant, wired to the signed-in persona
                                      └─ /api/webmind-token  → short-lived signed token
 assistant ── http://localhost:8080 ─▶ WebMind (index, analytics, permissions, audit)
```

## Run it: one command

```powershell
cd D:\WebMind
.\.venv\Scripts\Activate.ps1
python examples/run_demo.py            # add --llm none for instant, model-free answers
```

It starts WebMind (:8080) and the site (:8000) with matching secrets, waits until both are ready, and prints the one
URL to open: **http://localhost:8000/**. Press Ctrl+C to stop both. Logs: `examples/basic-website/data/logs/`.

If a port is already taken (an old `python -m http.server 8000`, an older `webmind serve`, a previous host app), the
launcher **refuses to start** and says which port. This matters: another server on 8000 does not know who is signed in,
so the assistant would treat everyone as a public visitor. Stop the old window, then run the command again.

First time only: `python examples/basic-website/setup_demo.py` (sample PDFs and `records.db`) and
`webmind index --config examples/basic-website/webmind.yaml`.

### Run the pieces yourself (two terminals)

Set the **same secrets in both** (the signing secret needs 32+ characters):

```powershell
$env:WEBMIND_JWT_SECRET    = "demo-signing-secret-for-local-testing-only-0123456789"
$env:WEBMIND_ADMIN_API_KEY = "demo-admin-key-local-0123456789"
webmind serve --config examples/basic-website/webmind-ollama.yaml     # terminal 1, http://localhost:8080
python examples/fastapi-integration/app.py                            # terminal 2, http://localhost:8000
```

## Who am I? Signing in

The assistant shows who it believes you are, under its title: **"Public visitor · Sign in"** or
**"Signed in as alice · reviewer"** (from `GET /v1/me`, the verified token). Everything it answers follows that identity.

- **Sign in** with the link in the assistant, the menu at the bottom left, or `http://localhost:8000/login`. You
  return to the page you were on.
- **Anonymous = public visitor.** An anonymous question that needs more access is answered with
  *"...sign in with an account that has access..."*.
- If it says "Public visitor" after you signed in, you are not on the host app's port: see the launcher note above.

## What to try

**Anonymous visitor** (public pages and public statistics only):

| Ask | Expect |
| --- | --- |
| Where can I find the eligibility requirements? | link to the Admissions page section, with sources |
| Generate a bar chart showing the number of projects by department. | bar chart (23, 24, 42, 31); **Export** → PNG / SVG / Excel / CSV |
| Show monthly metrics as a line chart for the available date range. | line chart, 11 months |
| What is the leave policy for staff? | nothing found (internal page hidden) |
| Show all project records / What is the average budget per department? | permission denied |

Also try opening `http://localhost:8000/internal/staff-handbook.html`: it is 403 until you browse as a staff persona.

**Personas** (bottom-left menu):

| Persona | Role / attributes | Try | Expect |
| --- | --- | --- | --- |
| `evan` | employee, engineering | What is the average budget per department? | engineering only; **Export** → Excel / CSV / JSON |
| `dana` | employee, research | Show me the engineering projects | no rows |
| `alice` | reviewer, engineering, Jan to Jun 2026 | Retrieve the records from my department between January and June. | 15 rows; **Export** → server download (Excel / CSV / PDF / JSON) |
| `alice` | | Retrieve the records from my department between July and November. | no rows, plus an access-limits note |
| `ada` | admin | Show monthly revenue as a line chart | revenue for all departments |
| `sam` | student | Show all project records | permission denied |

## Exports

| Result | Menu | Produced by |
| --- | --- | --- |
| Chart | Export → PNG, SVG, Excel, CSV | browser |
| Aggregate table | Export → Excel, CSV, JSON | browser |
| Answer | Save → Markdown, PDF (print dialog); Copy | browser |
| Raw record table (alice, evan) | Export → Excel, CSV, PDF, JSON | **server**, permission checked (`export` capability) |

Try as `evan` (employee, no `export` capability): exporting raw records is denied and the denial appears in the audit
log, while `alice` (reviewer) receives a file containing only her department and her window.

## Verify it yourself

```powershell
# Black-box: real HTTP requests, decisions, returned data vs the database, executed SQL, audit events
python examples/basic-website/verify_access_matrix.py --url http://127.0.0.1:8080

# Real browser (installed Chrome/Edge, headless, nothing downloaded): types into the actual widget
node examples/basic-website/browser_check.mjs --url http://localhost:8000/ `
     --question "Generate a bar chart showing the number of projects by department." `
     --expectChart bar --export "Export>PNG" --shot bar.png
# more: --pre http://localhost:8000/login/alice  --scheme dark  --viewport 390x844  --expandSources  --expectStream
```

Audit trail: `Invoke-RestMethod -Headers @{"X-WebMind-Admin-Key"=$env:WEBMIND_ADMIN_API_KEY} "http://localhost:8080/v1/admin/audit?limit=20"`
(each tool event records the user, decision, reason, row count and the SQL that ran).

## The permission model in one table

| Layer | Configured in | Demo setting |
| --- | --- | --- |
| Operation (capability) | `security.roles` | `public` may `analytics` (aggregates, charts) but not `records:read` or `export` |
| Dataset | `datasets[].access` | `projects` and `monthly_metrics` are public datasets |
| Field | `datasets[].columns[].access` | `name`, `budget`, `id` and `revenue` are staff-only |
| Row | `datasets[].row_filters` (`applies_to: protected_fields`) | staff see their own department for raw rows and protected fields |
| Date | `datasets[].date_window` | reviewers are clamped to their review window |
| Limits | `datasets[].max_rows`, `timeout_seconds` | 500 / 1000 rows, 10 s |
