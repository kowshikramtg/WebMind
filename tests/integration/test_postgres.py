"""PostgreSQL + pgvector integration (runs in CI; set WEBMIND_TEST_POSTGRES_URL locally).

Example: postgresql+psycopg://postgres:webmind@localhost:5432/postgres
"""

import os
import sqlite3
import uuid

import pytest
from sqlalchemy import create_engine, text
from tests.conftest import demo_config_dict, principal

from webmind.core.config import config_from_dict
from webmind.intelligence.analytics import AggregateQuery, RecordQuery
from webmind.runtime import WebMindRuntime

PG_URL = os.environ.get("WEBMIND_TEST_POSTGRES_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not PG_URL, reason="WEBMIND_TEST_POSTGRES_URL not set")]


@pytest.fixture
def pg_schema():
    """An isolated schema per test, dropped afterwards."""
    schema = f"wm_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_URL)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    yield schema
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def _url_with_schema(schema: str) -> str:
    sep = "&" if "?" in PG_URL else "?"
    return f"{PG_URL}{sep}options=-csearch_path%3D{schema},public"


async def test_pgvector_runtime_end_to_end(demo_dir, pg_schema):
    url = _url_with_schema(pg_schema)
    # Copy the demo records into PostgreSQL so the dataset path is exercised on PG too.
    src = sqlite3.connect(demo_dir / "generated" / "records.db")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE projects (id int, name text, department text, status text, created_at date, budget float)"
            )
        )
        c.execute(text("CREATE TABLE monthly_metrics (month date, department text, registrations int, revenue float)"))
        for row in src.execute("select * from projects"):
            c.execute(
                text("INSERT INTO projects VALUES (:a,:b,:c,:d,CAST(:e AS date),:f)"),
                dict(zip("abcdef", row, strict=True)),
            )
        for row in src.execute("select * from monthly_metrics"):
            c.execute(
                text("INSERT INTO monthly_metrics VALUES (CAST(:a AS date),:b,:c,:d)"),
                dict(zip("abcd", row, strict=True)),
            )
    eng.dispose()

    data = demo_config_dict(demo_dir)
    data["database"] = {"url": url}
    data["vector_store"] = {"provider": "pgvector"}
    for ds in data["datasets"]:
        ds["connection"] = url
    rt = WebMindRuntime(config_from_dict(data, base_dir=demo_dir))
    await rt.initialize()
    reports = await rt.sync()
    assert all(r.status == "succeeded" for r in reports)

    anon = rt.identity.anonymous()
    hits = await rt.retriever.search(anon, "eligibility requirements grade average")
    assert hits and "admissions.html" in (hits[0].url or "")
    assert all(
        "internal" not in (h.url or "") for h in await rt.retriever.search(anon, "staff leave policy annual leave")
    )
    other = rt.policy.build_principal(
        tenant_id="other", user_id="x", roles=["admin"], attributes={}, authenticated=True
    )
    assert await rt.retriever.search(other, "eligibility") == []

    mgr = principal(rt, "m", ["manager"])
    table, dims = await rt.datasets.aggregate(
        mgr,
        AggregateQuery(dataset="monthly_metrics", metrics=[{"fn": "sum", "column": "revenue"}], time_bucket="quarter"),
    )
    assert dims == ["period"] and table.rows[0]["period"] == "2026-Q1"
    rev = principal(rt, "r", ["reviewer"], department="engineering", review_from="2026-01-01", review_to="2026-03-31")
    rows = (await rt.datasets.query_records(rev, RecordQuery(dataset="projects"))).rows
    assert rows and {r["department"] for r in rows} == {"engineering"}

    engine = rt.datasets._engine(rt.cfg.dataset("projects"))
    with pytest.raises(Exception, match="(?i)read-only"), engine.begin() as c:
        c.execute(text("DELETE FROM projects"))
    await rt.close()
