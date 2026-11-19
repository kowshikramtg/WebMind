# Deployment

Docker and native deployments run the same code and the same `webmind.yaml` schema.

## Native

```bash
python -m venv .venv && source .venv/bin/activate
pip install "webmind[postgres,pdf,xlsx,reports]"      # drop extras you don't need
webmind init . --source ./site && webmind doctor && webmind index && webmind serve
```

SQLite (default) is fine for small and medium sites. For larger corpora or several
replicas, use PostgreSQL + pgvector:

```yaml
database: { url: ${WEBMIND_DATABASE_URL} }     # postgresql+psycopg://user:pass@host/db
vector_store: { provider: pgvector }           # the `vector` extension must be installable
```

A systemd unit is in `deployments/native/webmind.service`.

## Docker Compose

```bash
cp .env.example .env     # set POSTGRES_PASSWORD, WEBMIND_JWT_SECRET, WEBMIND_ADMIN_API_KEY
docker compose up -d
docker compose run --rm webmind webmind index
```

The stack runs the API (non-root, read-only filesystem, all capabilities dropped) and
PostgreSQL with pgvector. The API is bound to `127.0.0.1:8080`; put a TLS reverse
proxy in front. `--profile local-llm` adds Ollama. Existing PostgreSQL/Redis/model
services work too: change `WEBMIND_DATABASE_URL` and the `llm`/`embeddings` sections.
Content is mounted read-only from `WEBMIND_CONTENT_DIR`.

Note: the Docker image and compose file are provided and exercised by CI, but were not
built or run on the author's development machine for 0.1.0, which had no Docker. Run
`docker compose up` once in your environment and `webmind doctor` to confirm.

## Operations

- **Backups:** back up the metadata database (`pg_dump` / the SQLite file via `sqlite3 .backup`)
  and the object store directory. The index can always be rebuilt from sources with
  `webmind index --rebuild`, but conversation history, audit events and retained
  historical versions cannot.
- **Upgrades:** pin versions; read the changelog; run `webmind doctor` after upgrading.
  Schema creation is additive (`create_all`); 0.x releases have no migration tool yet.
- **Single process:** rate limiting and the sync scheduler are in-process. With several
  replicas, enable `scheduler` on only one, and apply rate limits at your proxy.
- **Observability:** logs go to stdout (`webmind.audit` records security decisions); query
  latency is stored in `query_metrics`. No telemetry leaves your infrastructure.
- **Reverse proxy:** `webmind serve --proxy-headers` with `FORWARDED_ALLOW_IPS` set to the
  proxy address; disable response buffering for `/v1/chat/stream`.
