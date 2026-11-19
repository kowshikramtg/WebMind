# Dependencies and licenses

WebMind is Apache-2.0. Core dependencies are installed always; the rest are optional extras
imported lazily.

| Package | Role | Extra | License (as published; re-verify at release) |
| --- | --- | --- | --- |
| fastapi, uvicorn, pydantic, httpx | API, validation, HTTP | core | MIT / BSD-3 / MIT / BSD-3 |
| sqlalchemy | database access | core | MIT |
| pyyaml, beautifulsoup4, zstandard, pyjwt | config, HTML, compression, tokens | core | MIT / MIT / BSD-3 / MIT |
| pypdf, python-docx, openpyxl, reportlab | PDF/DOCX parsing, XLSX/PDF export | `pdf`, `docx`, `xlsx`, `reports` | BSD-3 / MIT / MIT / BSD-3 |
| psycopg | PostgreSQL | `postgres` | **LGPL-3.0** (dynamic linking; fine for most uses, review for yours) |
| pymysql, cryptography, numpy | MySQL, RS/ES keys, faster vector math | `mysql`, `rsa`, `numpy` | MIT / Apache-2.0 or BSD / BSD-3 |
| anthropic | Claude provider | `anthropic` | MIT |
| langgraph | alternative orchestration engine | `langgraph` | MIT |
| sentence-transformers | local embeddings / cross-encoder rerank | `local-embeddings`, `rerank` | Apache-2.0 (model weights have their own licenses) |
| Docker images: pgvector/pgvector, ollama/ollama | optional services | — | PostgreSQL / MIT |

The license column comes from the maintainers' published metadata as understood when this
file was written. It has not been machine-verified. Before a release, run a license
report (for example `pip-licenses`) and update this table. CI runs `pip-audit` for known
vulnerabilities. Frontend SDK dev dependencies (react, typescript, @types/react) are
build-time only; the runtime package has `react` as a peer dependency.
