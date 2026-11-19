# Implementation status (0.1.0)

An honest map from the specification to what exists and what has been verified.
"Tested" means an automated test in this repository passes. "Untested here" means the
code exists but was not exercised in the author's environment.

| Area | Status |
| --- | --- |
| Config schema, validation, secrets via env | Done, tested |
| Provider interfaces; Ollama, OpenAI-compatible, Anthropic | Done. Ollama/OpenAI tested against mocked HTTP; Anthropic against a fake SDK client (request shape). **Not run against live services.** |
| Embeddings: hashing, Ollama, OpenAI-compatible | Done; hashing tested; remote ones mocked. sentence-transformers / cross-encoder: code only, **untested here**. |
| Website crawler, filesystem connector | Done, tested (crawler against an in-memory site) |
| HTML/Markdown/PDF/text parsing; DOCX | HTML/MD/PDF/text tested; **DOCX untested** |
| Sync: new/changed/deleted/unchanged, versioning, retention, CAS, embedding cache | Done, tested on SQLite |
| Hybrid retrieval (vector + FTS5/BM25, RRF), citations, navigation deep links | Done, tested (SQLite) |
| PostgreSQL FTS + pgvector | Done; test exists, runs in CI. **Not run locally**. |
| RBAC, ABAC, resource ACLs, tenant isolation, row filters, date windows | Done, tested |
| Read-only enforcement, audit log | Done, tested |
| Planner (rules + LLM), tool registry, native and LangGraph engines | Done, tested (LLM planner with scripted stand-in) |
| Analytics, comparison, correlation, charts, histograms | Done, tested on SQLite; quarter bucketing on PostgreSQL in the CI test; **MySQL untested** |
| Exports CSV/JSON/XLSX/PDF, expiring owner-only downloads | Done, tested |
| REST API, SSE streaming, webhooks, admin API, rate limiting | Done, tested |
| Widget, `@webmind/react`, `webmind-client` | Done; widget syntax-checked and its served file tested, **not browser-tested**; React client logic tested in Node, **React components compile but are not rendered in tests** |
| CLI (init/serve/doctor/index/sync/connectors/token/ask/eval) | Done, tested |
| Docker image / Compose | Written, **not built here** (no Docker available); CI builds the image |
| Telemetry (OpenTelemetry), Celery/Redis jobs, S3 storage, OpenSearch, Debezium CDC | **Not implemented**: sync uses an in-process scheduler, webhooks and CLI. The config reserves the telemetry section but it does nothing. |
| PostgreSQL RLS, schema migrations, load testing, groundedness eval | **Not implemented** |
| Natural-language understanding without an LLM | Rule-based: covers common phrasing in English (dates, grouping, metrics, follow-ups); unusual phrasing needs an LLM planner |

Retrieval quality with the built-in hashing embedder is lexical; see
[evaluation.md](evaluation.md) for the measured baseline and its limits.
