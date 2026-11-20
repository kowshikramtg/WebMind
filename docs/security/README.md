# Security model

## Principles

1. **Identity comes from the host application** as a signed JWT. WebMind never trusts
   identity from the model or unsigned request fields. Invalid tokens are rejected, never
   downgraded to anonymous.
2. **Authorization is deterministic and happens first.** `PolicyEngine` computes capabilities
   (RBAC with inheritance) and evaluates resource/dataset rules (roles, attributes, tenant).
   Retrieval pre-filters by the allowed resource ids inside the search query, so restricted
   text is never scored, returned or sent to an LLM.
3. **The model proposes, WebMind disposes.** A plan is just tool names and arguments. Every
   tool call is validated and checked against the caller's capabilities in
   `ToolRegistry.execute`. A model cannot add capabilities or widen a query.
4. **No model-written SQL.** Typed specs are compiled by `DatasetRegistry` to parameterized
   `SELECT`s using only configured columns. Tenant, row-level and date-window filters are
   appended unconditionally. Sessions are read-only at the driver (SQLite `mode=ro`,
   `query_only`; PostgreSQL/MySQL read-only transactions), with timeouts and row caps.
   Still give WebMind a read-only database role.
5. **Read-only is technical.** Connectors have no write methods; the config rejects
   `read_only_default: false`; write-like requests are refused and audited. The only
   things WebMind writes are its own index, audit log and expiring export files.
6. **Defense in depth.** Column-level access rules cover every use of a field (select, filter, group, aggregate, order, date range), so a hidden field cannot be probed through filters. Public analytics is a deliberate, per-dataset, per-field opt-in. Retrieved rows are re-checked against the principal after loading.
   Conversations are bound to tenant + user and store only questions and the last query
   spec, never earlier answers, and every follow-up is re-authorized.

## Hardening already in place

Object keys cannot escape the store root; the filesystem connector refuses paths and
symlinks outside its root; the crawler stays on one host, honors robots.txt, caps response
size and refuses sitemap DTDs; exports neutralize spreadsheet formulas and are owner-only with
a uniform 404; the widget renders server text with `textContent` and allows only http(s)
links; webhook and admin keys use constant-time comparison; responses carry
`nosniff`, `no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store`; secrets are
`SecretStr` and never logged; there is no telemetry.

## Prompt injection

Source text is wrapped as numbered, untrusted sources, and the system prompt says to ignore
instructions inside them. This reduces risk but is not a guarantee: a successful injection
can change the *wording* of an answer. It cannot change *what data is accessible*, because
the model never sees data the user can't access and cannot call tools they can't use.
Citations the model invents are stripped.

## Known limitations (0.1.0)

- Rate limiting is in-memory and per process.
- Anonymous visitors share the public corpus; a conversation id is unguessable but anonymous
  conversations are not identity-bound beyond that.
- Retained historical versions are not queryable, but they remain in object storage until
  retention removes them.
- PostgreSQL row-level security is not used; isolation is enforced in application queries.
- Audit events are in the same database as the data (no tamper-evidence).
- Dependency vulnerability scanning runs in CI (`pip-audit`), not as a gate on installs.

Report vulnerabilities privately: see [SECURITY.md](../../SECURITY.md).
