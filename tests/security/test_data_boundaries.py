"""Release-blocking data-boundary tests against the demo deployment."""

import sqlite3

import pytest
from sqlalchemy import text
from tests.conftest import principal

from webmind.core.errors import AuthorizationError, NotFoundError, QueryValidationError
from webmind.intelligence.analytics import AggregateQuery, Filter, RecordQuery
from webmind.storage.models import AuditEvent

pytestmark = pytest.mark.security


async def test_vector_and_keyword_search_never_return_restricted_content(runtime):
    anon = runtime.identity.anonymous()
    for q in ("leave policy staff accrue annual leave", "Staff Handbook expense policy", "26 days of annual leave"):
        results = await runtime.retriever.search(anon, q, k=10)
        assert all("internal/" not in (r.url or "") for r in results), q
        assert all("26 days" not in r.text for r in results)
    staff = principal(runtime, "e1", ["employee"], department="finance")
    results = await runtime.retriever.search(staff, "leave policy annual leave days", k=5)
    assert any("staff-handbook" in (r.url or "") for r in results)


async def test_attribute_restricted_pdf(runtime):
    q = "patent applications consulting engagements research report"
    finance = principal(runtime, "f1", ["employee"], department="finance")
    research = principal(runtime, "r1", ["employee"], department="research")
    assert not any("research-report" in (r.url or "") for r in await runtime.retriever.search(finance, q))
    assert any("research-report" in (r.url or "") for r in await runtime.retriever.search(research, q))


async def test_resource_listing_and_direct_retrieval_are_filtered(runtime):
    anon = runtime.identity.anonymous()
    listed = runtime.list_resources(anon)
    assert listed and all("internal" not in (r["url"] or "") for r in listed)
    staff = principal(runtime, "e1", ["employee"])
    internal = next(r for r in runtime.list_resources(staff) if "internal" in (r["url"] or ""))
    with pytest.raises(NotFoundError):
        runtime.resource_chunks(anon, internal["id"])
    assert runtime.resource_chunks(staff, internal["id"])


async def test_cross_tenant_isolation(runtime):
    other = runtime.policy.build_principal(
        tenant_id="other-tenant", user_id="x", roles=["admin"], attributes={}, authenticated=True
    )
    assert await runtime.retriever.search(other, "eligibility requirements admissions") == []
    assert runtime.list_resources(other) == []
    with pytest.raises(AuthorizationError):
        await runtime.datasets.query_records(other, RecordQuery(dataset="projects"))


async def test_row_filters_and_date_windows(runtime):
    reviewer = principal(
        runtime, "r1", ["reviewer"], department="engineering", review_from="2026-02-01", review_to="2026-03-31"
    )
    table = await runtime.datasets.query_records(
        reviewer, RecordQuery(dataset="projects", date_from="2026-01-01", date_to="2026-12-31")
    )
    assert table.rows
    assert {r["department"] for r in table.rows} == {"engineering"}
    assert all("2026-02-01" <= r["created_at"] <= "2026-03-31" for r in table.rows)
    # Asking for another department explicitly still only yields the user's own rows (none match both).
    t2 = await runtime.datasets.query_records(
        reviewer, RecordQuery(dataset="projects", filters=[Filter(column="department", value="finance")])
    )
    assert t2.rows == []
    # Missing attribute → no rows (deny by default), not "all rows".
    no_dept = principal(runtime, "r2", ["employee"])
    assert (await runtime.datasets.query_records(no_dept, RecordQuery(dataset="projects"))).rows == []
    # Managers are exempt from the department filter.
    mgr = principal(runtime, "m1", ["manager"])
    agg, _ = await runtime.datasets.aggregate(
        mgr, AggregateQuery(dataset="projects", metrics=[{"fn": "count"}], group_by=["department"])
    )
    assert len(agg.rows) == 4


async def test_anonymous_and_missing_capability_denied(runtime):
    with pytest.raises(AuthorizationError):
        await runtime.datasets.query_records(runtime.identity.anonymous(), RecordQuery(dataset="projects"))
    student = principal(runtime, "s1", ["student"])
    with pytest.raises(AuthorizationError):
        await runtime.datasets.query_records(student, RecordQuery(dataset="projects"))


@pytest.mark.parametrize(
    "spec",
    [
        {"dataset": "projects", "columns": ["id", "password"]},
        {"dataset": "projects", "filters": [{"column": "id", "value": 1}]},  # id is not filterable
        {"dataset": "projects", "order_by": "name; DROP TABLE projects"},
        {"dataset": "sqlite_master"},
    ],
)
async def test_query_specs_are_validated_against_allowlist(runtime, spec):
    mgr = principal(runtime, "m1", ["manager"])
    with pytest.raises(QueryValidationError):
        await runtime.datasets.query_records(mgr, RecordQuery.model_validate(spec))


async def test_injection_in_values_is_bound_not_executed(runtime, demo_dir):
    mgr = principal(runtime, "m1", ["manager"])
    evil = "engineering' OR '1'='1"
    t = await runtime.datasets.query_records(
        mgr, RecordQuery(dataset="projects", filters=[Filter(column="department", value=evil)])
    )
    assert t.rows == []
    t = await runtime.datasets.query_records(
        mgr, RecordQuery(dataset="projects", filters=[Filter(column="name", op="contains", value="%")])
    )
    assert t.rows == []  # LIKE wildcards are escaped
    con = sqlite3.connect(demo_dir / "generated" / "records.db")
    assert con.execute("select count(*) from projects").fetchone()[0] == 120


async def test_dataset_connections_are_technically_read_only(runtime, demo_dir):
    ds = runtime.cfg.dataset("projects")
    engine = runtime.datasets._engine(ds)
    with pytest.raises(Exception, match="(?i)readonly|read-only|attempt to write"), engine.begin() as conn:
        conn.execute(text("DELETE FROM projects"))
    con = sqlite3.connect(demo_dir / "generated" / "records.db")
    assert con.execute("select count(*) from projects").fetchone()[0] == 120


async def test_row_limit_enforced(runtime):
    mgr = principal(runtime, "m1", ["manager"])
    t = await runtime.datasets.query_records(mgr, RecordQuery(dataset="projects", limit=100_000))
    assert len(t.rows) <= runtime.cfg.dataset("projects").max_rows


async def test_exports_only_contain_authorized_rows_and_are_owner_only(runtime):
    reviewer = principal(
        runtime, "r1", ["reviewer"], department="engineering", review_from="2026-01-01", review_to="2026-06-30"
    )
    res = await runtime.orchestrator.chat(reviewer, "Export the project records into CSV")
    assert res.export is not None
    name, media, data = runtime.exports.open(reviewer, res.export.id)
    body = data.decode("utf-8-sig")
    assert "finance" not in body and "research" not in body and "engineering" in body
    other = principal(runtime, "r2", ["reviewer"], department="engineering")
    with pytest.raises(NotFoundError):
        runtime.exports.open(other, res.export.id)
    with pytest.raises(NotFoundError):
        runtime.exports.open(runtime.identity.anonymous(), res.export.id)


def test_csv_and_xlsx_formula_injection_neutralized():
    from webmind.core.types import TableResult
    from webmind.intelligence.exports import render

    t = TableResult(columns=["name"], rows=[{"name": '=HYPERLINK("http://evil")'}, {"name": "+1"}], total_rows=2)
    csv = render(t, "csv", "x").decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "'+1" in csv
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(render(t, "xlsx", "x")))
    assert wb["data"]["A2"].value.startswith("'=")
    assert wb["data"]["A2"].data_type == "s"


async def test_denials_and_write_attempts_are_audited(runtime):
    anon = runtime.identity.anonymous()
    r1 = await runtime.orchestrator.chat(anon, "Show all project records")
    assert r1.tools[0].status == "denied"
    r2 = await runtime.orchestrator.chat(
        principal(runtime, "e", ["employee"], department="x"), "Delete all records from the projects table"
    )
    assert r2.intent == "write_attempt" and not r2.tools
    with runtime.db.session() as s:
        actions = {(e.action, e.decision) for e in s.query(AuditEvent).all()}
    assert ("tool:query_records", "deny") in actions
    assert ("write_attempt", "deny") in actions


async def test_conversations_are_bound_to_their_owner(runtime):
    a = principal(runtime, "alice", ["employee"], department="engineering")
    b = principal(runtime, "bob", ["employee"], department="finance")
    first = await runtime.orchestrator.chat(a, "Show the project records from Q1")
    second = await runtime.orchestrator.chat(b, "Export these records into Excel", first.conversation_id)
    assert second.conversation_id != first.conversation_id  # bob cannot resume alice's conversation
