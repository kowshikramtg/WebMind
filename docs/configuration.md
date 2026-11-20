# Configuration reference

WebMind reads one YAML file (`webmind.yaml`, or `--config`, or `$WEBMIND_CONFIG`). It is
validated with typed Pydantic models: unknown keys are rejected and insecure settings fail
at startup. `${VAR}` and `${VAR:-default}` are replaced from the environment (and from a
`.env` next to the config file); an unset variable with no default is reported by name, and
an empty value counts as "not configured". Relative paths resolve against the config file's
directory.

| Section | Key options |
| --- | --- |
| `app` | `name`, `environment` (`development`/`staging`/`production`/`test`), `default_tenant` |
| `server` | `host`, `port`, `cors_origins`, `admin_api_key`, `rate_limit_per_minute` (0 = off), `max_query_chars`, `serve_widget` |
| `database` | `url` — `sqlite:///...` or `postgresql+psycopg://...` (metadata, keyword index, default vectors) |
| `vector_store` | `provider`: `database` (exact search, small/medium sites) or `pgvector` (needs PostgreSQL) |
| `object_store` | `provider: local`, `path` (originals, normalized text, exports) |
| `llm` | `provider`: `none` / `ollama` / `openai` (any OpenAI-compatible endpoint) / `anthropic`; `model`, `base_url`, `api_key`, `max_tokens`, `timeout_seconds`, `use_for_planning`; Anthropic only: `effort`, `refusal_fallback` |
| `embeddings` | `provider`: `hashing` (built-in lexical baseline) / `ollama` / `openai` / `sentence_transformers`; `model`, `dimensions`, `batch_size` |
| `retrieval` | `hybrid_search`, `top_k`, `candidate_k`, `rrf_k`, `min_semantic_score`, `reranking.provider` (`none`/`cross_encoder`) |
| `ingestion` | `chunk_size`, `chunk_overlap`, `store_originals`, `max_document_bytes` |
| `retention` | `keep_versions`, `export_ttl_minutes`, `audit_days` |
| `synchronization` | `enabled`, `scheduler` (run periodic syncs inside the API process) |
| `security` | `authentication`, `roles`, `read_only_default` (must be `true`) |
| `orchestration` | `engine`: `native` or `langgraph`; `max_tool_calls`, `history_turns` |
| `telemetry` | off by default; nothing is ever sent to a WebMind-operated service |
| `sources` | list of `website` / `filesystem` sources (below) |
| `datasets` | list of structured datasets (below) |

## Providers

- **No LLM** (`provider: none`): answers are deterministic extracts of the retrieved passages,
  with citations. Everything else (navigation, analytics, charts, exports) works.
- **Ollama / OpenAI-compatible**: plain HTTP. The OpenAI adapter works with any server that
  implements `/chat/completions` (vLLM, LM Studio, llama.cpp, hosted providers) via `base_url`.
- **Anthropic**: `pip install "webmind[anthropic]"`, `model: claude-opus-5-5` (or any current
  Claude model id). Sampling parameters are not sent. Server-side refusal fallback is on by
  default (`refusal_fallback: none` to disable); `effort` sets `output_config.effort`.
- Embedding model identity is stored in the index. If you change it, WebMind warns and
  `webmind index --reembed` rebuilds vectors without re-fetching sources.
- The built-in `hashing` embedder is lexical. It matches word forms and phrases, not
  paraphrases. Configure a neural embedder for semantic search.

## Sources

```yaml
sources:
  - id: website              # unique; becomes the connector id
    type: website            # or: filesystem
    url: https://example.org
    tenant: default          # optional; defaults to app.default_tenant
    use_sitemap: true
    crawl: true
    max_pages: 500
    max_depth: 5
    include: []              # regexes; if set, a URL must match one
    exclude: []              # regexes
    respect_robots_txt: true
    request_delay_seconds: 0.2
    access: { visibility: public }
    path_rules: []           # first match wins: { pattern: <regex>, access: {...} }
    sync: { interval_minutes: 60 }
    webhook_secret: ${HOOK_SECRET}
```

`filesystem` sources take `path`, `patterns` (globs), `exclude`, and `base_url` (public URL for
citation links). Hidden files and anything resolving outside `path` are skipped.

An **access rule** is `{visibility: public}` or `{visibility: restricted, roles: [...],
attributes: {key: value|[values]}}`. Restricted with no roles/attributes means "any signed-in
user of the tenant". A user needs one listed role and every listed attribute.

## Datasets

See [INTEGRATION.md §3.2](../INTEGRATION.md#32-structured-data-datasets). Column flags:
`filterable` (default true), `groupable`, `aggregatable`; `values` lists known categories.
`row_filters` force `column == principal attribute` (missing attribute = no rows);
`date_window` clamps queries to dates held in principal attributes; `unless_roles` exempts roles.

**Publishing aggregates without a login.** Authorization has four independent layers; a request must
pass all of them:

1. *Capability* (`security.roles`): `analytics` allows aggregates and charts, `records:read` allows raw
   rows, `export` allows downloads. Give `public` only `analytics` to allow anonymous statistics.
2. *Dataset* (`datasets[].access`): `visibility: public` lets anyone with the capability use the dataset.
3. *Field* (`columns[].access`): a column with its own access rule can only be referenced (selected,
   filtered, grouped, aggregated, ordered, or used for a date range) by principals that rule admits.
   Naming a hidden field is denied and audited; hidden fields are silently left out of default
   projections and default chart metrics. Filtering on a hidden field is denied too, so it cannot be
   used to probe values.
4. *Row/date policy* (`row_filters`, `date_window`): `applies_to: all_queries` (default) always applies.
   `applies_to: protected_fields` applies only to raw-row queries and to queries that read a protected
   column, so aggregates over unprotected fields stay dataset-wide for everyone (staff never see less
   than the public view).

```yaml
security:
  roles:
    public: { capabilities: [chat, search, navigate, "documents:read", analytics] }   # no records:read, no export
datasets:
  - id: projects
    access: { visibility: public }
    columns:
      - { name: department, type: string, groupable: true }                           # public
      - { name: budget, type: number, aggregatable: true,
          access: { visibility: restricted, roles: [employee, admin] } }              # staff only
    row_filters:
      - { column: department, equals_attribute: department, unless_roles: [admin], applies_to: protected_fields }
```

The SQL that ran for each tool call (with literal values) is stored in the audit event's `details.sql`;
it is never returned to API clients.

## Security keys

```yaml
security:
  authentication:
    mode: host_application      # or public
    allow_anonymous: true
    anonymous_roles: [public]
    jwt: { algorithms: [HS256], secret: ..., issuer: ..., audience: ...,
           max_token_lifetime_seconds: 3600, leeway_seconds: 30,
           user_id_claim: sub, roles_claim: roles, tenant_claim: tenant, attributes_claim: attributes }
  roles:
    <name>: { capabilities: [...], inherits: [...] }
```

Capabilities: `chat`, `search`, `navigate`, `documents:read`, `records:read`, `analytics`,
`export`, `reports`, `sync:status`. HS* secrets must be ≥ 32 characters; mixing HS* and
RS*/ES* algorithms is rejected; RS*/ES* need `public_key` (PEM or file path, requires the
`rsa` extra).
