# Analytics and authentication integration report

Scope: public analytics without login, plus authenticated personas, in the Lumen Valley demo
(WebMind 0.1.0). Verified 2026-11-20 against a running server, real signed tokens, the Ollama model
`llama3.2:latest`, and a real headless Chrome.

## 1. Root cause

Two requests, "bar chart of projects by department" and "monthly metrics as a line chart", returned
*"You don't have permission to access that information."* The denial was correct for the configuration
that existed. Three independent gaps made public analytics impossible:

| # | Gap | Evidence |
| --- | --- | --- |
| 1 | The anonymous principal has only the `public` role, whose capabilities were `chat, search, navigate, documents:read`. It lacked **`analytics`**, so the tool registry denied `generate_chart` before any query was built. | Audit: `tool:generate_chart DENY (missing capabilities: analytics)` |
| 2 | Both datasets were `visibility: restricted` to employee+ roles, so even with the capability the dataset ACL would refuse: `restricted content requires authentication`. | `can_access(anon, dataset.access)` returned `allowed=False` |
| 3 | There was **no field-level policy**, and the department row filter and reviewer date window had no way to say "this applies to sensitive fields, not to published aggregates". Opening the datasets would have silently returned zero rows to visitors (no `department` attribute) and would have exposed `budget`, project names and revenue. | `_scope()` applied row filters unconditionally; columns had no `access` |

Security was never "disabled" to fix this; the model was extended so a dataset owner can publish
specific aggregates explicitly.

## 2. Permission model implemented

A request must pass **every** layer (tools call the same `PolicyEngine`/`DatasetRegistry` as document
retrieval, and there is no bypass path):

| Layer | Question it answers | Configured in | Demo value |
| --- | --- | --- | --- |
| 1. Capability | May this principal do this *kind* of operation? | `security.roles` | `public`: `analytics` (aggregates, charts). Not `records:read` (raw rows), not `export`. |
| 2. Dataset | May they touch this dataset? | `datasets[].access` | `projects`, `monthly_metrics`: `public` |
| 3. Field (**new**) | May they reference this column at all (select, filter, group, aggregate, order, date range)? | `datasets[].columns[].access` | `id`, `name`, `budget` (projects) and `revenue` (monthly_metrics): staff roles only |
| 4. Row / date | Which rows and dates? | `row_filters`, `date_window` with **`applies_to: protected_fields`** (**new**) | staff: own `department`; reviewer: clamped to `review_from..review_to`; admin and manager exempt |
| 5. Limits | How much? | `max_rows`, `timeout_seconds`, read-only connection | 500 / 1000 rows, 10 s, SQLite `mode=ro` |

Design points:

- **Aggregates are public only where the dataset owner said so.** With `public` lacking `records:read`, anonymous
  callers can never retrieve individual rows or exports, even for public fields.
- **`applies_to: protected_fields`** makes the row and date policies bind raw-row queries and any query that
  reads a protected column. Aggregates over unprotected columns are institute-wide for everyone, so a
  signed-in employee never sees *less* than the anonymous public view (an early design mistake I avoided:
  a department filter on public counts would have shown an employee 1 department while a visitor saw 4).
- **Named hidden fields are denied and audited; unnamed ones are skipped.** Asking for `budget` is a denial.
  A default chart or projection quietly leaves hidden fields out, so "monthly metrics" for a visitor charts
  registrations only and never silently substitutes or reveals revenue.
- **Filtering on a hidden field is denied** (a `budget > 100000` filter would otherwise be a probing oracle).
- **The LLM planner never sees hidden columns** (`view_for`), and its plans still go through the same checks.
- **SQL is audited, never returned.** Every allowed tool call stores the executed, parameter-rendered SQL in
  `audit_events.details.sql`; API responses exclude it (`TableResult.executed_sql` is `exclude=True`).
- **Transparency.** When policy narrowed a result, the answer ends with a deterministic line, for example
  `Access limits applied to this result: department = engineering (your department); …permitted window…`,
  so an empty result is never mistaken for missing data.

Demo personas (host app, `examples/fastapi-integration/app.py`, signed 5-minute tokens):

| Persona | Role / attributes | Sees |
| --- | --- | --- |
| anonymous | `public` | public pages; project counts by department/status/date; monthly registrations |
| `evan` | employee, department=engineering | + budgets and raw records for **engineering** only; internal handbook |
| `dana` | employee, department=research | + budgets/revenue/records for **research** only |
| `alice` | reviewer, engineering, window 2026-01-01..2026-06-30 | engineering records inside the window; Excel export of those rows |
| `ada` | admin | all departments, all dates, revenue and budgets |
| `sam` | student | public data only; denied raw records |

## 3. Files modified

Source: `core/config.py` (column `access`, `applies_to`), `core/types.py` (audit-only `executed_sql`),
`intelligence/analytics.py` (field enforcement, protected-field scoping, SQL capture, window wording),
`intelligence/planner.py` (column-aware defaults), `intelligence/orchestrator.py` (planner wiring,
deterministic highlights, access-limits note, conditional correlation rule),
`intelligence/tools.py` (SQL into audit), `api/static/webmind-widget.js` (full chart renderer),
`cli/main.py` (console-safe doctor output).

Config and examples: `examples/basic-website/webmind.yaml` (demo policy; explicitly justified here),
`examples/basic-website/webmind-ollama.yaml` (identical to `webmind.yaml` except the `llm` block),
`examples/fastapi-integration/app.py` (personas), new `examples/basic-website/DEMO.md`,
`verify_access_matrix.py`, `browser_check.mjs`.

SDK: `sdk/react/src/ChartView.tsx` (label truncation), `sdk/react/test/chart.test.mjs`, `sdk/react/package.json`.

Tests: `tests/security/test_public_analytics.py` (14), `tests/unit/test_highlights.py` (5),
`tests/integration/test_orchestration_engines.py` (extended).

Docs: this report, `docs/configuration.md`, `docs/security/README.md`, `docs/status.md`, `README.md`.

Not modified: the demo databases (`records.db` row counts verified unchanged: 120 / 44), `webmind.yaml` sources
and storage settings, tenant isolation, audit logging, the Ollama provider, document question answering.

## 4. Live verification (real HTTP requests)

`examples/basic-website/verify_access_matrix.py` against `webmind serve -c webmind-ollama.yaml`
(real Ollama answers, signed tokens, admin audit API). For each case it checks the authorization decision,
the returned data **against ground truth read straight from `records.db`**, the executed SQL, and the audit event.

**Result: 32 of 32 cases passed** (an earlier full run also passed 32/32). The final run executed immediately before a one-line change to the wording of the out-of-window note; that case was re-verified in the browser afterwards and by the unit/security tests.

### Public (no token)

| Request | Decision | Result | Audit / SQL evidence |
| --- | --- | --- | --- |
| "Explain the services offered by this website." | allowed | cited answer from `services.html` | retrieval (not tool-audited) |
| Bar chart of projects by department | **ALLOW** `tool:generate_chart` | 4 bars: 23/24/42/31 = database | `SELECT projects.department, count(*) … WHERE 1 = 1 GROUP BY projects.department … LIMIT 501`: no budget, no name, no department predicate |
| Monthly metrics line chart | **ALLOW** | 11 points; series `registrations` only; sums = database | `SELECT strftime('%Y-%m', month) AS period, sum(registrations) … GROUP BY period`; revenue never in SQL |
| "What is the leave policy for staff?" | n/a | nothing found; no internal URL or "26 days" | restricted page invisible to retrieval |
| "Show all project records" | **DENY** | no table | `missing capabilities: records:read` |
| "What is the average budget per department?" | **DENY** | no data | `field 'budget' of dataset 'projects' is not available to this user` |
| "Show monthly revenue as a line chart" | **DENY** | no chart | `field 'revenue' … not available` |
| "Export the projects into Excel" | **DENY** | no export | `missing capabilities: records:read, export` |
| API raw records | 403 | | `api:analytics:records DENY` |
| API `sum(budget)` | 403 | | `api:analytics:aggregate DENY (field 'budget')` |
| API filter `budget > 100000` (probing) | 403 | no inference oracle | DENY (field 'budget') |
| API group by `name` | 422 | rejected (not groupable) | validation |
| API value `engineering'; DROP TABLE projects;--` | 200 | zero rows; table intact (120 rows) | value is a bound parameter |
| API `limit: 10000`, day buckets | 200 | 44 rows ≤ `max_rows` | row cap enforced |

### Authenticated

| Persona and request | Decision | Result | SQL evidence |
| --- | --- | --- | --- |
| evan: average budget per department | ALLOW | **engineering only**, value = database | `… WHERE projects.department = 'engineering' …` |
| evan: engineering projects | ALLOW | 24 rows = database, all engineering | department predicate |
| dana (research): "engineering projects" | ALLOW, 0 rows | unauthorized department yields no data | `department = 'research' AND department = 'engineering'` |
| evan: projects by department (public fields) | ALLOW | all 4 departments (never less than the public view) | no department predicate |
| dana: monthly revenue chart | ALLOW | 11 points = **research-only** revenue | `WHERE monthly_metrics.department = 'research'` |
| alice (reviewer): Jan to June | ALLOW | 15 rows = database; all engineering and in-window | `department = 'engineering' AND created_at >= '2026-01-01' AND <= '2026-06-30'` |
| alice: July to November (outside window) | ALLOW, 0 rows | window clamps; answer states the limit | clamped predicate, `2026-06-30` upper bound |
| alice: export Jan to June to Excel | ALLOW | `.xlsx`, 15 rows = database | same predicates; owner-only download |
| ada (admin): average budget | ALLOW | 4 departments = database | `WHERE 1 = 1` |
| ada: monthly revenue chart | ALLOW | 11 points = all-department revenue | `WHERE 1 = 1` |
| evan: leave policy (internal page) | allowed | grounded, cites `staff-handbook` | |
| sam (student): leave policy | n/a | nothing found, nothing leaked | |
| evan: research report PDF (attribute rule) | allowed | cites `research-report` | |
| sam: research report PDF | n/a | not cited | |
| sam: all project records | **DENY** | | `missing capabilities: records:read` |
| forged token (wrong secret) / tampered token | 401 | never downgraded to anonymous | |

## 5. Visualization pipeline in a real browser

Pipeline checked end to end: natural language → intent (`visualization`) → authorization → structured query →
deterministic SQL result → chart spec → API response → **SVG drawn by the real widget in headless Chrome**.
`browser_check.mjs` clicks the launcher, types the question and presses Enter in the actual widget, waits
for the answer, then reads the DOM back and compares the drawn values to the database. It uses an installed
Chrome with a throwaway profile; the Claude-in-Chrome extension was not connected in this session, so it was not used.

| Page / persona | Question | Drawn in the browser | Matches database |
| --- | --- | --- | --- |
| public page | bar chart, projects by department | `svg[data-wm-chart=bar]`, 4 `<rect>`, y axis 0..42, x labels, legend; heights monotonic in value | yes (23/24/42/31) |
| public page | monthly metrics line chart | 1 `<polyline>` + 11 points, x labels 2026-01..2026-11, registrations only | yes (11 values) |
| host app, `ada` | monthly revenue line chart | 11 points, series "Sum revenue" | yes (all departments) |
| host app, `dana` | same question | 11 points, **different** values | yes (research only) |
| host app, `alice` | records Jan to June | table, 15 data rows, no other department | yes |
| host app, `alice` | records July to November | no table; "Access limits applied… outside your permitted window" | yes (0 rows) |
| host app, `sam` | all project records | permission message, no table | yes |

Screenshots: [bar](images/browser_public_bar.png), [line](images/browser_public_line.png),
[admin revenue](images/browser_ada_revenue.png), [research revenue](images/browser_dana_revenue.png),
[reviewer in window](images/browser_alice_in_window.png), [reviewer outside window](images/browser_alice_out_of_window.png),
[student denied](images/browser_sam_denied.png).

`@webmind/react`'s `ChartView` is covered by a structural test (bar/line/pie marks, proportional heights, tooltips).
It was not rendered in a DOM; the widget is the component verified in a browser.

## 6. Automated test results

- Python: **140 passed, 1 skipped** (the skip is the PostgreSQL test, which needs `WEBMIND_TEST_POSTGRES_URL`).
  `pytest -m security`: 51 passed.
- New security tests (14): anonymous aggregates over public fields with no policy predicate; refusal of protected
  metrics, filters and group-bys; no raw rows or export for anonymous; row policy on protected fields and raw rows;
  reviewer window; staff never see less than public; hidden fields omitted from default projections and denied when
  named; planner never defaults to hidden fields; LLM planning view hides columns; SQL audited but absent from responses.
- JavaScript: 6 passed (client streaming/auth, `safeHref`, chart structure).
- Lint and format clean (`ruff`), widget and driver syntax-checked.

## 7. Defects found while verifying, and fixed

| Defect | How it was found | Fix |
| --- | --- | --- |
| LLM prose mis-ranked groups (said research (31) is lower than engineering (24)) | real-browser run | highest/lowest/total are computed deterministically (`highlights`); the model is told to copy, not rank |
| Spurious "correlation does not imply causation" note on a plain chart | real-browser run | correlation rule is included only when a coefficient is in the result |
| An out-of-window request looked like "no data" | reviewer browser run | deterministic "Access limits applied…" note; clearer wording when fully outside the window |
| Widget chart: no axis labels or legend, no pie, low-contrast labels in dark mode | screenshots | full SVG renderer with axes, legend, pie, dark-mode colors |
| X labels truncated ("engineerin") in widget **and** React `ChartView` | browser run; new React test | limit raised to 12 characters in both |
| `webmind doctor` crashed on Windows consoles (✔/→) and misreported a working LLM as unreachable | earlier run | ASCII-safe output |

## 8. Remaining limitations

- **Public aggregates are still data disclosure.** Counts by department/status/day are visible to anyone. Fine
  grained buckets over a small table could narrow to individuals; there is no minimum-group-size rule yet.
- **Histograms read raw column values.** They are limited to columns the principal may use and still pass row policy,
  but a per-column "aggregate-only" mode would be stricter.
- Field rules are per column, not per (column, aggregate function). A public `sum` implies a public `avg`.
- The rule-based planner only understands common English phrasing. Unusual phrasing needs the LLM planner,
  which the experimental config disables (`use_for_planning: false`).
- A 3B local model can still produce awkward prose. Numbers, charts and rankings are deterministic, but its wording
  is not verified beyond the highlight and number checks above.
- The host app's cookie "login" is a demo toy; production hosts must use their real authentication.
- Verified in headless Chrome only; Firefox, Safari and mobile were not tested. The React components were not rendered in a DOM.
- No load testing; rate limiting and the sync scheduler are in-process.
- The live matrix needs Ollama running; its LLM-written answers take 5 to 80 s each on CPU.
- No changes were committed or pushed.
