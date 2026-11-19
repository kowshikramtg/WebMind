# Contributing to WebMind

Thanks for your interest in improving WebMind! This document explains how to set up a
development environment and what we expect from contributions.

## Ground rules

WebMind's architecture rules (see [ARCHITECTURE.md](ARCHITECTURE.md)) are binding for
all contributions. The most important ones:

1. **Interfaces before providers.** New LLM / embedding / storage / connector support is
   added by implementing an existing interface, never by special-casing the core.
2. **The LLM never authorizes anything.** Access decisions belong to `webmind.security`.
3. **No unrestricted SQL.** Structured data is only queried through the dataset query
   compiler (`webmind.intelligence.analytics`).
4. **Read-only is a security boundary.** Connectors and dataset sessions must not be able to
   modify sources.
5. **Never silently substitute a mock** for a configured real provider.
6. **Do not claim a feature works unless it is tested.** Update `docs/status.md` when you
   add or change a capability.
7. **Keep the core light.** Heavy dependencies go into optional extras with lazy imports.

## Development setup

```bash
git clone https://github.com/kowshikramtg/WebMind.git
cd WebMind
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

React SDK:

```bash
cd sdk/react
npm install
npm run build
```

PostgreSQL integration tests run when `WEBMIND_TEST_POSTGRES_URL` points at a database
with the `vector` extension available, e.g.

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=webmind pgvector/pgvector:pg16
export WEBMIND_TEST_POSTGRES_URL=postgresql+psycopg://postgres:webmind@localhost:5432/postgres
pytest -m postgres
```

## Pull requests

- Keep PRs focused; one logical change per PR.
- Add tests. Security-relevant changes **must** add tests under `tests/security/`
  (these are release-blocking in CI).
- Run `ruff check` and `pytest` locally.
- Update documentation in `docs/` when behavior or configuration changes.
- New dependencies must have an OSI-approved license compatible with Apache-2.0; add them
  to `docs/dependencies.md`.
- By contributing you agree that your contributions are licensed under the Apache License 2.0.

## Writing a connector

See [docs/connectors/README.md](docs/connectors/README.md).

## Reporting bugs and security issues

Bugs: open a GitHub issue with reproduction steps. Security issues: **do not** open a
public issue — follow [SECURITY.md](SECURITY.md).
