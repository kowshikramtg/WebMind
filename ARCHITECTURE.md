# Architecture

WebMind is a **modular monolith**: one Python package with explicit layer boundaries,
composed in a single root (`webmind.runtime.WebMindRuntime`) that backs the HTTP API, the
CLI and the in-process SDK. Docker and native deployments run identical code.

```
Integration   widget.js · @webmind/react · webmind-client · REST · Python SDK
API           api/app.py   auth → principal, rate limit, SSE, webhooks, admin
Intelligence  orchestrator ─ planner (rules | LLM) ─ tools ─ retrieval/rag │ analytics │ exports
Governance    security/identity (JWT) · policy (RBAC+ABAC+tenant) · audit
Adapters      providers (LLM, embeddings, rerank) · storage (db, vectors, FTS, object store) · connectors
Your data     sites, documents, databases (read-only)
```

## Request flow

1. The host app signs a short-lived JWT; `IdentityVerifier` produces a `Principal`
   (tenant, roles, attributes, capabilities) or an anonymous principal.
2. `NativeOrchestrator.begin` loads the conversation only if it belongs to this tenant and user.
3. The planner turns the question into `Plan(intent, calls[])`. `RuleBasedPlanner` is
   deterministic and needs no LLM; `LLMPlanner` asks the model for a structured plan and
   falls back to rules if the plan is invalid.
4. `ToolRegistry.execute` checks capabilities, validates arguments against the tool's
   input model, enforces a timeout and writes audit events. This is the policy enforcement point.
5. Tools do the work under the principal's authority: retrieval filters to accessible
   resource ids *inside* the search query; analytics compiles validated specs with mandatory
   policy filters.
6. Synthesis: documents → grounded LLM answer with enforced citations (or an extractive
   answer without an LLM); data → deterministic summary (or an LLM explaining only the
   returned rows). Results carry citations, tables, chart specs, exports, tool traces.
7. Only questions and the last query spec are persisted as conversation state.

## Modules (`src/webmind`)

| Module | Responsibility |
| --- | --- |
| `core` | typed config, domain types, errors |
| `providers` | LLM / embedding / reranker interfaces and adapters |
| `storage` | SQLAlchemy models, database bootstrap, keyword + vector search, CAS object store |
| `ingestion` | connectors (website, filesystem), parsers, chunking, sync engine |
| `security` | identity, policy engine, audit |
| `intelligence` | retrieval, RAG, planner, tools, analytics, exports, orchestrators |
| `api`, `cli`, `sdk`, `runtime`, `evaluation` | delivery surfaces |

## Key decisions

- **Source stays the source of truth.** Chunks, vectors and normalized text are derived;
  originals are optional (`store_originals`) and stored compressed, content-addressed
  and deduplicated. Resource identity, versions and indexed chunks are separate tables.
- **Safe replacement on change.** A changed resource gets a new version; its chunks and
  vectors are swapped in one transaction. Errors keep the old index and are annotated.
  A resource is deleted only if discovery was complete, or if the source confirms it is gone.
- **Semantic layer instead of text-to-SQL.** Query specs are typed, allowlisted and compiled
  with SQLAlchemy Core. The LLM can choose fields; it cannot emit SQL. Numbers come from the
  database.
- **Orchestration is replaceable.** `Orchestrator` is an interface; the LangGraph engine
  reuses the same steps, so swapping engines cannot change security behavior.
- **Honest defaults.** No LLM ⇒ extractive answers; built-in embeddings are lexical;
  both are labeled as such by `webmind doctor`. A mock is never substituted silently for a
  configured provider: provider errors surface as warnings with extractive fallback.
- **Single-process assumptions** (rate limiter, scheduler, vector cache) are documented in
  [docs/deployment.md](docs/deployment.md).

## Data model

`tenants`, `connectors`, `resources`, `resource_versions`, `documents`, `chunks`, `embeddings`
(+ `embedding_cache`), `sync_jobs`, `conversations`, `conversation_messages`, `audit_events`,
`export_jobs`, `query_metrics`, `index_settings`. Users, roles and permissions are not
tables: identities come from the host application's tokens and role→capability mappings
live in versioned configuration.

See [docs/status.md](docs/status.md) for what is implemented versus planned.
