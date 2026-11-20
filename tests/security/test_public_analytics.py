"""Public aggregate analytics + field-level policy (release-blocking)."""

import pytest
from tests.conftest import demo_config_dict, principal

from webmind.core.config import config_from_dict
from webmind.core.errors import AuthorizationError, QueryValidationError
from webmind.intelligence.analytics import AggregateQuery, Filter, RecordQuery
from webmind.runtime import WebMindRuntime
from webmind.storage.models import AuditEvent

pytestmark = pytest.mark.security

COUNT_BY_DEPT = AggregateQuery(dataset="projects", metrics=[{"fn": "count"}], group_by=["department"])


async def test_anonymous_can_aggregate_public_fields_institute_wide(runtime):
    anon = runtime.identity.anonymous()
    table, dims = await runtime.datasets.aggregate(anon, COUNT_BY_DEPT)
    assert dims == ["department"] and len(table.rows) == 4
    assert sum(r["count_records"] for r in table.rows) == 120
    sql = " ".join(table.executed_sql.split()).lower()
    assert "budget" not in sql and "projects.name" not in sql and "department =" not in sql
    monthly, _ = await runtime.datasets.aggregate(
        anon,
        AggregateQuery(
            dataset="monthly_metrics", metrics=[{"fn": "sum", "column": "registrations"}], time_bucket="month"
        ),
    )
    assert len(monthly.rows) == 11


@pytest.mark.parametrize(
    "query",
    [
        AggregateQuery(dataset="projects", metrics=[{"fn": "sum", "column": "budget"}]),  # protected metric
        AggregateQuery(
            dataset="projects", metrics=[{"fn": "count"}], filters=[Filter(column="budget", op="gt", value=1)]
        ),
        AggregateQuery(dataset="monthly_metrics", metrics=[{"fn": "sum", "column": "revenue"}], time_bucket="month"),
        AggregateQuery(
            dataset="monthly_metrics", metrics=[{"fn": "avg", "column": "revenue"}], group_by=["department"]
        ),
    ],
)
async def test_anonymous_cannot_touch_protected_fields(runtime, query):
    with pytest.raises(AuthorizationError, match="not available"):
        await runtime.datasets.aggregate(runtime.identity.anonymous(), query)


async def test_anonymous_cannot_read_raw_rows_or_export(runtime):
    anon = runtime.identity.anonymous()
    with pytest.raises(AuthorizationError):
        await runtime.datasets.query_records(anon, RecordQuery(dataset="projects"))
    # Not even via the "analytics" capability: histograms and chart tools read raw column values.
    with pytest.raises(AuthorizationError):
        await runtime.datasets.query_records(
            anon, RecordQuery(dataset="projects", columns=["budget"]), capability="analytics"
        )
    # Public fields via analytics-capability raw path are still dataset-scoped but carry no identifiers.
    ok = await runtime.datasets.query_records(
        anon, RecordQuery(dataset="projects", columns=["department", "status"]), capability="analytics"
    )
    assert set(ok.columns) == {"department", "status"}


async def test_non_groupable_fields_rejected_before_policy(runtime):
    with pytest.raises(QueryValidationError):
        await runtime.datasets.aggregate(
            runtime.identity.anonymous(),
            AggregateQuery(dataset="projects", metrics=[{"fn": "count"}], group_by=["name"]),
        )


async def test_employee_sees_at_least_the_public_view(runtime):
    employee = principal(runtime, "e1", ["employee"], department="engineering")
    table, _ = await runtime.datasets.aggregate(employee, COUNT_BY_DEPT)
    assert len(table.rows) == 4 and sum(r["count_records"] for r in table.rows) == 120  # public fields: not narrowed


async def test_row_policy_applies_to_protected_fields_and_raw_rows(runtime):
    employee = principal(runtime, "e1", ["employee"], department="engineering")
    avg, _ = await runtime.datasets.aggregate(
        employee,
        AggregateQuery(dataset="projects", metrics=[{"fn": "avg", "column": "budget"}], group_by=["department"]),
    )
    assert [r["department"] for r in avg.rows] == ["engineering"]
    assert "department = 'engineering'" in table_sql(avg)
    rows = await runtime.datasets.query_records(employee, RecordQuery(dataset="projects"))
    assert {r["department"] for r in rows.rows} == {"engineering"}
    # Raw rows are protected even when only public columns are requested.
    only_public = await runtime.datasets.query_records(
        employee, RecordQuery(dataset="projects", columns=["department", "status"])
    )
    assert {r["department"] for r in only_public.rows} == {"engineering"}
    admin = principal(runtime, "a1", ["admin"])
    all_avg, _ = await runtime.datasets.aggregate(
        admin, AggregateQuery(dataset="projects", metrics=[{"fn": "avg", "column": "budget"}], group_by=["department"])
    )
    assert len(all_avg.rows) == 4


async def test_reviewer_date_window_binds_protected_queries_only(runtime):
    reviewer = principal(
        runtime, "r1", ["reviewer"], department="engineering", review_from="2026-01-01", review_to="2026-03-31"
    )
    outside = await runtime.datasets.query_records(
        reviewer, RecordQuery(dataset="projects", date_from="2026-07-01", date_to="2026-11-30")
    )
    assert outside.rows == []  # clamped window, not an error
    assert any("permitted window" in f for f in outside.applied_filters)
    counts, _ = await runtime.datasets.aggregate(  # public fields: window does not apply
        reviewer, AggregateQuery(dataset="projects", metrics=[{"fn": "count"}], date_from="2026-07-01")
    )
    assert counts.rows[0]["count_records"] > 0


async def test_hidden_fields_are_omitted_from_default_projection_and_denied_when_named(demo_dir):
    data = demo_config_dict(demo_dir)
    data["security"]["roles"]["student"] = {"inherits": ["public"], "capabilities": ["records:read"]}
    rt = WebMindRuntime(config_from_dict(data, base_dir=demo_dir))
    await rt.initialize()
    student = principal(rt, "s1", ["student"])
    t = await rt.datasets.query_records(student, RecordQuery(dataset="projects", limit=3))
    assert set(t.columns) == {"department", "status", "created_at"}  # id/name/budget silently omitted
    with pytest.raises(AuthorizationError, match="'budget'"):
        await rt.datasets.query_records(student, RecordQuery(dataset="projects", columns=["budget"]))
    with pytest.raises(AuthorizationError):
        await rt.datasets.query_records(student, RecordQuery(dataset="projects", order_by="budget"))
    await rt.close()


async def test_planner_never_defaults_to_hidden_fields(runtime):
    anon = runtime.identity.anonymous()
    plan = runtime.orchestrator.rules.plan(
        "Show monthly metrics as a line chart for the available date range.", anon, list(runtime.cfg.datasets)
    )
    metrics = plan.calls[0].arguments["query"]["metrics"]
    assert metrics == [{"fn": "sum", "column": "registrations"}]
    # An explicit request for a protected field is planned, then denied and audited (not silently swapped).
    res = await runtime.orchestrator.chat(anon, "Show monthly revenue as a line chart")
    assert res.tools[0].status == "denied" and res.chart is None
    with runtime.db.session() as s:
        denies = [e for e in s.query(AuditEvent).filter_by(action="tool:generate_chart", decision="deny")]
    assert any("revenue" in (e.reason or "") for e in denies)


async def test_llm_planning_view_hides_protected_columns(runtime):
    anon = runtime.identity.anonymous()
    ds = runtime.cfg.dataset("projects")
    view = runtime.datasets.view_for(anon, ds)
    assert {c.name for c in view.columns} == {"department", "status", "created_at"}
    assert {c.name for c in runtime.datasets.view_for(principal(runtime, "e", ["employee"]), ds).columns} == {
        c.name for c in ds.columns
    }


async def test_executed_sql_is_audited_but_never_returned_to_clients(runtime):
    from fastapi.testclient import TestClient
    from tests.conftest import token

    from webmind.api.app import create_app

    with TestClient(create_app(runtime=runtime)) as client:
        r = client.post(
            "/v1/chat", json={"message": "Generate a bar chart showing the number of projects by department."}
        )
        assert r.status_code == 200 and r.json()["chart"]["type"] == "bar"
        assert "select" not in r.text.lower() and "executed_sql" not in r.text
        admin = client.get("/v1/admin/audit", headers={"X-WebMind-Admin-Key": "admin-key-for-tests-0123456789"}).json()[
            "events"
        ]
        allowed = [e for e in admin if e["action"] == "tool:generate_chart" and e["decision"] == "allow"]
        assert allowed and "count(*)" in allowed[0]["details"]["sql"].lower()
        # Authenticated staff get the same shape plus protected fields.
        staff = {"Authorization": f"Bearer {token('e1', ['employee'], department='engineering')}"}
        r2 = client.post("/v1/chat", json={"message": "What is the average budget per department?"}, headers=staff)
        assert [row["department"] for row in r2.json()["table"]["rows"]] == ["engineering"]


def table_sql(table) -> str:
    return " ".join((table.executed_sql or "").split())
