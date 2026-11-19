# Security Policy

WebMind is security-sensitive infrastructure: it sits between end users, language
models and an organization's private data. We take reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Security" tab → "Report a vulnerability") on this repository.

Please include:

- affected version / commit,
- the configuration involved (redact secrets),
- reproduction steps or a proof of concept,
- the impact you believe it has (e.g. cross-tenant read, permission bypass, write to a source).

We aim to acknowledge reports within 5 working days and to agree on a disclosure
timeline with the reporter. Credit is given unless you prefer otherwise.

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Scope — what counts as a vulnerability

Examples of in-scope issues:

- Retrieval, analytics or exports returning data the principal is not authorized to see.
- Cross-tenant data access.
- Any path through which WebMind writes to a connected source while read-only mode is active.
- Executing SQL that was not produced by WebMind's query compiler.
- Prompt-injection that changes authorization decisions (as opposed to answer wording).
- Authentication bypass (e.g. accepting unsigned/expired tokens, algorithm confusion).
- Path traversal in the object store or filesystem connector.
- Leakage of secrets (API keys, JWT secrets, DB credentials) via API responses or logs.

Out of scope: the content quality of LLM answers, denial of service through very large
self-hosted crawls you configured yourself, and vulnerabilities in third-party model
providers.

## Security model (summary)

See [docs/security/README.md](docs/security/README.md) for the full model. In short:

- Identity is supplied by the host application as a signed JWT; WebMind never trusts
  identity claims from the model or from unsigned request fields.
- Authorization is computed by a deterministic policy engine **before** any content is
  retrieved; the LLM only ever sees content the principal may access.
- Read-only access to sources is enforced technically (read-only DB sessions, a query
  compiler that can only emit `SELECT`, connectors without write methods).
- All allow/deny decisions for sensitive operations are written to an audit log.
