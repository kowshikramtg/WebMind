"""The first end-to-end demonstration (spec §24), exercised through the HTTP API."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from tests.conftest import token

from webmind.api.app import create_app

REVIEWER = token("rev-1", ["reviewer"], department="engineering", review_from="2026-01-01", review_to="2026-06-30")
MANAGER = token("mgr-1", ["manager"])


@pytest.fixture
def client(runtime):
    with TestClient(create_app(runtime=runtime)) as c:
        yield c


def ask(client, message, tok=None, conversation_id=None):
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    r = client.post("/v1/chat", json={"message": message, "conversation_id": conversation_id}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_readiness(client):
    assert client.get("/v1/health").json()["status"] == "ok"
    ready = client.get("/v1/health/ready").json()
    assert ready["ready"] and ready["checks"]["keyword_index"] == "ok"
    assert client.get("/v1/health").headers["x-content-type-options"] == "nosniff"


def test_scenario_a_public_visitor_gets_cited_answer(client):
    res = ask(client, "Explain the services offered by this website.")
    assert res["intent"] == "document_qa" and res["grounded"]
    assert res["citations"] and any("services.html" in c["url"] for c in res["citations"])
    assert "Undergraduate" in res["answer"] or "services" in res["answer"].lower()
    assert res["citations"][0]["freshness"]["mode"] == "indexed"


def test_scenario_b_navigation_points_to_exact_section(client):
    res = ask(client, "Where can I find the eligibility requirements?")
    assert res["intent"] == "navigation"
    assert res["navigation"][0]["url"] == "http://localhost:8000/admissions.html#eligibility"
    assert "admissions.html#eligibility" in res["answer"]


def test_scenario_c_reviewer_gets_department_and_date_restricted_records(client):
    res = ask(client, "Retrieve the records from my department between January and June.", REVIEWER)
    assert res["intent"] == "records"
    rows = res["table"]["rows"]
    assert rows and {r["department"] for r in rows} == {"engineering"}
    assert all("2026-01-01" <= r["created_at"] <= "2026-06-30" for r in rows)
    assert any("your department" in f for f in res["table"]["applied_filters"])


def test_scenario_d_analytics_with_chart(client):
    res = ask(client, "Compare monthly performance across departments.", MANAGER)
    chart = res["chart"]
    assert chart is not None and chart["type"] in ("line", "bar")
    assert {s["key"] for s in chart["series"]} == {"engineering", "research", "admissions", "finance"}
    assert len(chart["data"]) == 11  # Jan–Nov 2026 in the demo data
    # Values come from SQL, not from the model: spot-check against the table.
    table_sum = sum(r["sum_registrations"] for r in res["table"]["rows"] if r["period"] == "2026-01")
    chart_sum = sum(v for k, v in chart["data"][0].items() if k != "period")
    assert table_sum == chart_sum


def test_scenario_e_export_to_excel_follow_up(client):
    first = ask(client, "Retrieve the records from my department between January and June.", REVIEWER)
    res = ask(client, "Export these records into Excel.", REVIEWER, first["conversation_id"])
    assert res["intent"] == "export"
    ref = res["export"]
    assert ref["format"] == "xlsx" and ref["row_count"] == len(first["table"]["rows"])
    dl = client.get(ref["download_url"], headers={"Authorization": f"Bearer {REVIEWER}"})
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    # Another user — even with the same role — cannot download it.
    other = token("rev-2", ["reviewer"], department="engineering")
    assert client.get(ref["download_url"], headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert client.get(ref["download_url"]).status_code == 404


async def test_scenario_f_data_changes_are_detected(runtime, demo_dir):
    anon = runtime.identity.anonymous()
    page = demo_dir / "site" / "admissions.html"
    page.write_text(page.read_text(encoding="utf-8").replace("March 31", "April 20"), encoding="utf-8")
    (report,) = await runtime.sync("website")
    assert report.changed == 1 and report.unchanged + report.not_modified == 5
    res = await runtime.orchestrator.chat(anon, "What is the main application deadline?")
    assert "April 20" in res.answer and "March 31" not in res.answer
    # Deletion: the page disappears from retrieval.
    (demo_dir / "site" / "scholarships.html").unlink()
    (report,) = await runtime.sync("website")
    assert report.deleted == 1
    hits = await runtime.retriever.search(anon, "merit scholarship need-based grants")
    assert all("scholarships.html" not in (h.url or "") for h in hits)
    # Unchanged sync touches nothing.
    (report,) = await runtime.sync("website")
    assert report.changed == report.new == report.deleted == 0


def test_scenario_g_unauthorized_operations_are_rejected_and_logged(client):
    res = ask(client, "Show all project records with their budgets")
    assert res["tools"][0]["status"] == "denied" and "permission" in res["answer"]
    res = ask(client, "Delete the records from the projects table", MANAGER)
    assert res["intent"] == "write_attempt" and "read-only" in res["answer"]
    r = client.post("/v1/analytics/query", json={"kind": "records", "query": {"dataset": "projects"}})
    assert r.status_code == 403
    audit = client.get("/v1/admin/audit", headers={"X-WebMind-Admin-Key": "admin-key-for-tests-0123456789"}).json()
    decisions = {(e["action"], e["decision"]) for e in audit["events"]}
    assert ("write_attempt", "deny") in decisions and ("tool:query_records", "deny") in decisions


def test_invalid_token_is_401_not_anonymous(client):
    r = client.post("/v1/chat", json={"message": "hi"}, headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    r = client.post("/v1/chat", json={"message": "hi"}, headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_admin_endpoints_require_key(client):
    assert client.get("/v1/admin/audit").status_code == 401
    assert client.get("/v1/admin/audit", headers={"X-WebMind-Admin-Key": "wrong"}).status_code == 401
    assert (
        client.post(
            "/v1/admin/sync", json={}, headers={"X-WebMind-Admin-Key": "admin-key-for-tests-0123456789"}
        ).status_code
        == 202
    )


def test_search_resources_and_sync_status(client):
    r = client.post("/v1/search", json={"query": "tuition fees"})
    assert r.status_code == 200 and r.json()["results"]
    assert all("internal" not in (x["url"] or "") for x in client.get("/v1/resources").json()["items"])
    assert client.get("/v1/sync/status").status_code == 403  # anonymous lacks sync:status
    admin = token("adm", ["admin"])
    status = client.get("/v1/sync/status", headers={"Authorization": f"Bearer {admin}"}).json()
    assert status["resources"]["active"] == 8


def test_analytics_and_export_endpoints(client):
    h = {"Authorization": f"Bearer {MANAGER}"}
    r = client.post(
        "/v1/analytics/query",
        json={
            "kind": "aggregate",
            "query": {
                "dataset": "monthly_metrics",
                "metrics": [{"fn": "sum", "column": "revenue"}],
                "group_by": ["department"],
            },
        },
        headers=h,
    )
    assert r.status_code == 200 and len(r.json()["table"]["rows"]) == 4
    r = client.post(
        "/v1/analytics/query",
        json={"kind": "records", "query": {"dataset": "projects", "columns": ["secret"]}},
        headers=h,
    )
    assert r.status_code == 422
    r = client.post("/v1/exports", json={"query": {"dataset": "projects", "limit": 5}, "format": "csv"}, headers=h)
    assert r.status_code == 200
    csv = client.get(r.json()["download_url"], headers=h)
    assert csv.headers["content-type"].startswith("text/csv") and csv.text.count("\n") == 6


def test_streaming_endpoint(client):
    with client.stream("POST", "/v1/chat/stream", json={"message": "What are the tuition fees?"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    assert events[0]["type"] == "plan" and events[-1]["type"] == "final"
    assert "4,800" in events[-1]["response"]["answer"]


def test_webhook_signature(client, runtime):
    runtime.cfg.sources[0].webhook_secret = __import__("pydantic").SecretStr("hook-secret")
    body = json.dumps({"uris": ["admissions.html"]}).encode()
    sig = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    assert (
        client.post("/v1/webhooks/website", content=body, headers={"X-WebMind-Signature": "sha256=bad"}).status_code
        == 401
    )
    assert client.post("/v1/webhooks/website", content=body, headers={"X-WebMind-Signature": sig}).status_code == 202
    assert client.post("/v1/webhooks/documents", content=body, headers={"X-WebMind-Signature": sig}).status_code == 404


def test_widget_is_served(client):
    r = client.get("/v1/widget.js")
    assert r.status_code == 200 and "textContent" in r.text and "innerHTML" not in r.text.replace("never innerHTML", "")
