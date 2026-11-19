# REST API

A running server serves interactive OpenAPI docs at `/docs` (schema at `/openapi.json`).
Authentication: `Authorization: Bearer <user token>` (see INTEGRATION.md §4). Requests
without a token are anonymous if `allow_anonymous` is true. Invalid tokens return `401`.
Admin endpoints use `X-WebMind-Admin-Key` and are disabled unless `server.admin_api_key` is set.

| Endpoint | Notes |
| --- | --- |
| `POST /v1/chat` | Body `{message, conversation_id?}` → `ChatResponse`: `answer`, `intent`, `citations[]`, `navigation[]`, `table`, `chart`, `export`, `statistics`, `tools[]` (status `ok`/`denied`/`error`), `grounded`, `llm_used`, `warnings[]`. |
| `POST /v1/chat/stream` | Server-sent events: `plan`, `token`*, `final` (`response` = ChatResponse), or `error`. |
| `POST /v1/search` | `{query, top_k?}` → permission-filtered chunks with scores, deep links, freshness. |
| `POST /v1/analytics/query` | `{kind: "records"\|"aggregate", query: {...}}`; specs are validated against dataset allowlists. |
| `POST /v1/exports` | `{query, format: csv\|json\|xlsx\|pdf, title?}` → reference with `download_url`. |
| `GET /v1/exports/{id}/download` | Only the creating user, until `expires_at`. Others get 404. |
| `GET /v1/resources` | Resources visible to the caller. |
| `GET /v1/sync/status` | Needs `sync:status`. |
| `GET /v1/health`, `/v1/health/ready` | Liveness; readiness checks the database and keyword index. |
| `POST /v1/webhooks/{source_id}` | HMAC-signed (`X-WebMind-Signature: sha256=<hex>`); body `{"uris": [...]}` optional. |
| `POST /v1/admin/sync`, `/v1/admin/reembed`, `GET /v1/admin/audit`, `GET /v1/admin/sync/status` | Admin key required. |
| `GET /v1/widget.js` | The embeddable widget. |

Status codes: `401` bad/missing credentials, `403` missing capability, `404` unknown or
not-visible object (no existence oracle), `422` invalid query spec, `429` rate limited.
A denied *tool* inside a chat turn returns `200` with `tools[].status = "denied"` and a
generic message; the detail goes to the audit log.

Query spec examples:

```json
{"kind": "records", "query": {"dataset": "projects", "date_from": "2026-01-01", "date_to": "2026-06-30",
  "filters": [{"column": "status", "op": "eq", "value": "active"}], "limit": 100}}
{"kind": "aggregate", "query": {"dataset": "monthly_metrics", "metrics": [{"fn": "sum", "column": "revenue"}],
  "group_by": ["department"], "time_bucket": "month"}}
```
