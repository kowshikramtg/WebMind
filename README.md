# WebMind

**Open-source, self-hosted website intelligence.** Add a permission-aware assistant to a
website, dashboard or internal app: cited answers, "where do I find…" navigation,
natural-language records, statistics, charts and exports. It runs on your infrastructure
with your choice of model, and it never writes to your data.

> Status: **0.1.0 alpha.** The core works and is tested, but several pieces are unverified
> against live services, and some spec items are not built. Read [docs/status.md](docs/status.md)
> before relying on it.

## What it does

- **Ingests** websites (sitemap + crawl, robots.txt) and files (HTML, Markdown, text, PDF, DOCX),
  keeps them in sync incrementally (conditional requests, fingerprints, versions, deletions).
- **Answers** from retrieved passages only, with citations and deep links to the exact section.
  Hybrid search (vectors + full text, rank fusion), optional reranking.
- **Queries structured data** (SQLite, PostgreSQL, MySQL) through validated query specs,
  never model-written SQL, with row-level and date-window policies.
- **Analyzes and visualizes**: aggregates, period comparison, correlation (with a causation
  caveat), bar/line/area/pie/histogram specs, CSV/JSON/XLSX/PDF exports.
- **Enforces permissions** before anything reaches a model: roles, attributes, tenants,
  per-path rules, audit log, read-only sources.
- **Works with any LLM** (Ollama, OpenAI-compatible servers, Claude) or none at all.

## Quick start

```bash
git clone https://github.com/kowshikramtg/WebMind.git && cd WebMind
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run the Lumen Valley demo (spec §24)
python examples/basic-website/setup_demo.py              # sample PDFs + database
export WEBMIND_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
webmind index --config examples/basic-website/webmind.yaml
webmind ask "Where can I find the eligibility requirements?" -c examples/basic-website/webmind.yaml
webmind serve -c examples/basic-website/webmind.yaml     # http://127.0.0.1:8080/docs
```

Your own site: `webmind init . --source https://your.site && webmind index && webmind serve`.

## Integrate it

**[INTEGRATION.md](INTEGRATION.md)** is the how-to: script-tag widget, React/Next.js,
Python in-process SDK, REST from any language, signing user tokens (Python/Node/PHP),
WordPress, syncing, and adding WebMind to your repository and Compose file.

```tsx
<WebMindProvider endpoint="/webmind-api" getAccessToken={getSessionToken}>
  <WebMindChat />
</WebMindProvider>
```

```python
assistant = WebMind(config="webmind.yaml"); await assistant.initialize()
res = await assistant.ask("Explain the latest project updates", user_id="user_123", roles=["employee"])
```

## Documentation

| | |
| --- | --- |
| [INTEGRATION.md](INTEGRATION.md) | Integrate into your website or app |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules, data flow, design decisions |
| [docs/configuration.md](docs/configuration.md) | Every config option |
| [docs/api.md](docs/api.md) | REST API |
| [docs/security/README.md](docs/security/README.md) | Threat model and limits |
| [docs/deployment.md](docs/deployment.md) | Native, Docker, operations |
| [docs/connectors/README.md](docs/connectors/README.md) | Writing a connector |
| [docs/evaluation.md](docs/evaluation.md) | Retrieval and security evaluation |
| [docs/status.md](docs/status.md) | What is and isn't done and verified |
| [docs/dependencies.md](docs/dependencies.md) | Dependency licenses |

## Development

```bash
pytest                    # 100+ tests; `pytest -m security` is the release-blocking gate
ruff check src tests && ruff format --check src tests
cd sdk/react && npm ci && npm run build && npm test
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Privacy

WebMind has no accounts, phone-home or telemetry. Your data goes only where you configure
it: if you choose a hosted LLM or embedding provider, the retrieved passages (and for data
questions, the authorized result rows) are sent to that provider. Use Ollama or another
local model to keep everything on your hardware.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
