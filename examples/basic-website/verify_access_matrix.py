"""Black-box verification of public + authenticated access against a RUNNING WebMind server.

For every case it sends a real HTTP request (with a signed token where the persona is signed in),
then checks the authorization decision, the returned data (against ground truth read directly from
the demo database), the SQL that was executed, and the audit event the request produced.

    export WEBMIND_JWT_SECRET=...   WEBMIND_ADMIN_API_KEY=...      # same values the server uses
    webmind serve -c examples/basic-website/webmind-ollama.yaml    # in another terminal
    python examples/basic-website/verify_access_matrix.py [--url http://127.0.0.1:8080] [--json out.json]

Exit code is non-zero if any expectation fails. Read-only: it never modifies data or the index.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from webmind.security.identity import issue_token

HERE = Path(__file__).resolve().parent
DB = HERE / "generated" / "records.db"

PERSONAS: dict[str, dict[str, Any] | None] = {
    "anonymous": None,
    "evan": {"roles": ["employee"], "attributes": {"department": "engineering"}},
    "dana": {"roles": ["employee"], "attributes": {"department": "research"}},
    "alice": {
        "roles": ["reviewer"],
        "attributes": {"department": "engineering", "review_from": "2026-01-01", "review_to": "2026-06-30"},
    },
    "ada": {"roles": ["admin"], "attributes": {}},
    "sam": {"roles": ["student"], "attributes": {}},
}


def truth(sql: str, *params: Any) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


class Harness:
    def __init__(self, url: str, secret: str, admin_key: str) -> None:
        self.url = url.rstrip("/")
        self.secret = secret
        self.admin = {"X-WebMind-Admin-Key": admin_key}
        self.http = httpx.Client(timeout=180)
        self.last_ts = ""
        self.results: list[dict[str, Any]] = []
        self.failures = 0
        self.last_ts = self._audit_max_ts()

    def _headers(self, persona: str) -> dict[str, str]:
        spec = PERSONAS[persona]
        if spec is None:
            return {}
        tok = issue_token(
            self.secret, user_id=persona, roles=spec["roles"], attributes=spec["attributes"], ttl_seconds=300
        )
        return {"Authorization": f"Bearer {tok}"}

    def _audit(self) -> list[dict[str, Any]]:
        r = self.http.get(f"{self.url}/v1/admin/audit", params={"limit": 200}, headers=self.admin)
        r.raise_for_status()
        return r.json()["events"]

    def _audit_max_ts(self) -> str:
        ev = self._audit()
        return max((e["ts"] for e in ev), default="")

    def new_audit(self) -> list[dict[str, Any]]:
        ev = [e for e in self._audit() if e["ts"] > self.last_ts]
        if ev:
            self.last_ts = max(e["ts"] for e in ev)
        return sorted(ev, key=lambda e: e["ts"])

    def case(self, name: str, persona: str, call, check) -> dict[str, Any]:
        """call(headers) -> httpx.Response ; check(resp, body, audit_events) -> list[(label, ok)]"""
        t0 = time.perf_counter()
        resp = call(self._headers(persona))
        ms = round((time.perf_counter() - t0) * 1000)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        audit = self.new_audit()
        try:
            checks = check(resp, body, audit)
        except Exception as exc:  # a malformed response is a failed expectation, not a crashed run
            checks = [(f"check raised {type(exc).__name__}: {exc}", False)]
        ok = all(c[1] for c in checks)
        self.failures += not ok
        rec = {
            "name": name,
            "persona": persona,
            "http": resp.status_code,
            "ms": ms,
            "intent": body.get("intent"),
            "tools": [(t["tool"], t["status"]) for t in body.get("tools", [])],
            "rows": len(body["table"]["rows"]) if body.get("table") else None,
            "chart": (
                {
                    "type": body["chart"]["type"],
                    "points": len(body["chart"]["data"]),
                    "series": [s["key"] for s in body["chart"]["series"]],
                }
                if body.get("chart")
                else None
            ),
            "answer": (body.get("answer") or body.get("detail") or "")[:140].replace("\n", " "),
            "audit": [
                {
                    "user": e["user"],
                    "action": e["action"],
                    "decision": e["decision"],
                    "reason": e["reason"],
                    "rows": e["details"].get("rows"),
                    "sql": e["details"].get("sql"),
                }
                for e in audit
            ],
            "checks": [{"check": label, "ok": good} for label, good in checks],
            "pass": ok,
        }
        self.results.append(rec)
        mark = "PASS" if ok else "FAIL"
        print(f"\n[{mark}] {name}  (persona={persona}, HTTP {resp.status_code}, {ms} ms)")
        print(f"   intent={rec['intent']} tools={rec['tools']} rows={rec['rows']} chart={rec['chart']}")
        print(f"   answer: {rec['answer']}")
        for e in rec["audit"]:
            print(
                f"   audit : {e['user']} {e['action']} -> {e['decision'].upper()}"
                + (f" ({e['reason']})" if e["reason"] else "")
                + (f" rows={e['rows']}" if e["rows"] is not None else "")
            )
            if e["sql"]:
                print(f"   sql   : {' '.join(e['sql'].split())[:230]}")
        for label, good in checks:
            print(f"   {'ok  ' if good else 'FAIL'}  {label}")
        return rec

    # -- convenience -----------------------------------------------------------
    def chat(self, name, persona, question, check):
        return self.case(
            name,
            persona,
            lambda h: self.http.post(f"{self.url}/v1/chat", json={"message": question}, headers=h),
            check,
        )


def audit_has(audit, action, decision, reason_contains=None):
    for e in audit:
        if e["action"] == action and e["decision"] == decision:
            if reason_contains is None or reason_contains in (e["reason"] or ""):
                return True
    return False


def audit_sql(audit):
    return " ".join((next((e["details"].get("sql") for e in audit if e["details"].get("sql")), "") or "").split())


def run(h: Harness) -> None:
    by_dept = dict(truth("select department, count(*) from projects group by department"))
    reg_by_month = dict(truth("select substr(month,1,7), sum(registrations) from monthly_metrics group by 1"))

    print("=" * 78 + "\nPUBLIC (anonymous, no token)\n" + "=" * 78)

    h.chat(
        "public: website question",
        "anonymous",
        "Explain the services offered by this website.",
        lambda r, b, a: [
            ("HTTP 200 and document_qa", r.status_code == 200 and b["intent"] == "document_qa"),
            ("answer is cited from services.html", any("services.html" in (c["url"] or "") for c in b["citations"])),
        ],
    )

    def chk_bar(r, b, a):
        got = {row["department"]: row["count_records"] for row in b["table"]["rows"]} if b.get("table") else {}
        return [
            ("authorized: generate_chart ok", b["tools"][0]["status"] == "ok"),
            (
                "bar chart with 4 departments",
                bool(b["chart"]) and b["chart"]["type"] == "bar" and len(b["chart"]["data"]) == 4,
            ),
            ("counts equal ground truth from records.db", got == by_dept),
            ("audit: allow tool:generate_chart", audit_has(a, "tool:generate_chart", "allow")),
            (
                "executed SQL is a parameter-free aggregate, no raw columns",
                "count(*)" in audit_sql(a).lower()
                and "budget" not in audit_sql(a)
                and "projects.name" not in audit_sql(a),
            ),
            (
                "SQL has no department/date policy predicate (public fields only, institute-wide)",
                "department =" not in audit_sql(a),
            ),
        ]

    h.chat(
        "public: projects by department (bar)",
        "anonymous",
        "Generate a bar chart showing the number of projects by department.",
        chk_bar,
    )

    def chk_line(r, b, a):
        data = {d["period"]: d["sum_registrations"] for d in b["chart"]["data"]} if b.get("chart") else {}
        return [
            ("authorized: generate_chart ok", b["tools"][0]["status"] == "ok"),
            ("line chart, 11 monthly points", bool(b["chart"]) and b["chart"]["type"] == "line" and len(data) == 11),
            (
                "series limited to public field: registrations only (revenue withheld)",
                [s["key"] for s in b["chart"]["series"]] == ["sum_registrations"],
            ),
            (
                "monthly sums equal ground truth",
                {k: int(v) for k, v in data.items()} == {k: int(v) for k, v in reg_by_month.items()},
            ),
            ("audit: allow tool:generate_chart", audit_has(a, "tool:generate_chart", "allow")),
            ("SQL never touches revenue", "revenue" not in audit_sql(a)),
        ]

    h.chat(
        "public: monthly metrics (line)",
        "anonymous",
        "Show monthly metrics as a line chart for the available date range.",
        chk_line,
    )

    h.chat(
        "public: restricted internal information",
        "anonymous",
        "What is the leave policy for staff?",
        lambda r, b, a: [
            (
                "no internal content in answer",
                "26 days" not in b["answer"] and "annual leave" not in b["answer"].lower(),
            ),
            ("no internal URLs cited", all("internal/" not in (c["url"] or "") for c in b["citations"])),
            ("not grounded (nothing accessible found)", b["grounded"] is False),
        ],
    )

    h.chat(
        "public: raw project records",
        "anonymous",
        "Show all project records",
        lambda r, b, a: [
            ("tool denied", b["tools"][0]["status"] == "denied"),
            ("no table returned", b.get("table") is None),
            (
                "audit: deny tool:query_records (missing records:read)",
                audit_has(a, "tool:query_records", "deny", "records:read"),
            ),
        ],
    )

    h.chat(
        "public: protected field (budget)",
        "anonymous",
        "What is the average budget per department?",
        lambda r, b, a: [
            ("tool denied", b["tools"][0]["status"] == "denied"),
            ("no budget data returned", b.get("table") is None and "budget" not in json.dumps(b.get("table"))),
            ("audit: deny with field reason", audit_has(a, "tool:calculate_statistics", "deny", "budget")),
        ],
    )

    h.chat(
        "public: protected field (revenue chart)",
        "anonymous",
        "Show monthly revenue as a line chart",
        lambda r, b, a: [
            ("tool denied", b["tools"][0]["status"] == "denied"),
            ("audit: deny with field reason", audit_has(a, "tool:generate_chart", "deny", "revenue")),
        ],
    )

    h.chat(
        "public: export attempt",
        "anonymous",
        "Export the projects into Excel",
        lambda r, b, a: [
            ("tool denied, no export", b["tools"][0]["status"] == "denied" and b.get("export") is None),
            ("audit: deny tool:export_data", audit_has(a, "tool:export_data", "deny")),
        ],
    )

    h.case(
        "public: API raw records",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query", headers=hd, json={"kind": "records", "query": {"dataset": "projects"}}
        ),
        lambda r, b, a: [
            ("HTTP 403", r.status_code == 403),
            ("audit: deny api:analytics:records", audit_has(a, "api:analytics:records", "deny")),
        ],
    )

    h.case(
        "public: API aggregate of protected field",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query",
            headers=hd,
            json={
                "kind": "aggregate",
                "query": {
                    "dataset": "projects",
                    "metrics": [{"fn": "sum", "column": "budget"}],
                    "group_by": ["department"],
                },
            },
        ),
        lambda r, b, a: [
            ("HTTP 403", r.status_code == 403),
            ("audit: deny api:analytics:aggregate (field)", audit_has(a, "api:analytics:aggregate", "deny", "budget")),
        ],
    )

    h.case(
        "public: API aggregate by project name (grouping a non-groupable field)",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query",
            headers=hd,
            json={
                "kind": "aggregate",
                "query": {"dataset": "projects", "metrics": [{"fn": "count"}], "group_by": ["name"]},
            },
        ),
        lambda r, b, a: [("rejected (HTTP 422)", r.status_code == 422)],
    )

    h.case(
        "public: API filter probing a protected field",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query",
            headers=hd,
            json={
                "kind": "aggregate",
                "query": {
                    "dataset": "projects",
                    "metrics": [{"fn": "count"}],
                    "filters": [{"column": "budget", "op": "gt", "value": 100000}],
                },
            },
        ),
        lambda r, b, a: [("HTTP 403 (no inference oracle)", r.status_code == 403)],
    )

    h.case(
        "public: API SQL-injection-looking values stay data",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query",
            headers=hd,
            json={
                "kind": "aggregate",
                "query": {
                    "dataset": "projects",
                    "metrics": [{"fn": "count"}],
                    "filters": [{"column": "department", "op": "eq", "value": "engineering'; DROP TABLE projects;--"}],
                },
            },
        ),
        lambda r, b, a: [
            ("HTTP 200 with zero rows", r.status_code == 200 and b["table"]["rows"] in ([], [{"count_records": 0}])),
            ("table still intact", truth("select count(*) from projects")[0][0] == 120),
        ],
    )

    h.case(
        "public: row limit honoured (explicit limit above dataset max)",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/analytics/query",
            headers=hd,
            json={
                "kind": "aggregate",
                "query": {
                    "dataset": "monthly_metrics",
                    "metrics": [{"fn": "sum", "column": "registrations"}],
                    "group_by": ["department"],
                    "time_bucket": "day",
                    "limit": 10000,
                },
            },
        ),
        lambda r, b, a: [
            ("HTTP 200", r.status_code == 200),
            ("rows <= dataset.max_rows (1000)", len(b["table"]["rows"]) <= 1000),
        ],
    )

    print("\n" + "=" * 78 + "\nAUTHENTICATED (signed tokens)\n" + "=" * 78)

    eng_avg = dict(
        truth("select department, round(avg(budget),2) from projects where department='engineering' group by 1")
    )

    def chk_evan_avg(r, b, a):
        rows = b["table"]["rows"] if b.get("table") else []
        return [
            ("authorized", b["tools"][0]["status"] == "ok"),
            (
                "only engineering returned (row policy on protected field)",
                [x["department"] for x in rows] == ["engineering"],
            ),
            ("avg equals ground truth", rows and round(rows[0]["avg_budget"], 2) == eng_avg["engineering"]),
            ("SQL carries the department predicate", "department = 'engineering'" in audit_sql(a)),
            ("audit: allow", audit_has(a, "tool:calculate_statistics", "allow")),
        ]

    h.chat("auth: engineering employee, avg budget", "evan", "What is the average budget per department?", chk_evan_avg)

    h.chat(
        "auth: employee, own-department records",
        "evan",
        "Show me the engineering projects",
        lambda r, b, a: [
            ("authorized, rows returned", b["tools"][0]["status"] == "ok" and b["table"]["total_rows"] > 0),
            ("all rows engineering", {x["department"] for x in b["table"]["rows"]} == {"engineering"}),
            ("count equals ground truth", b["table"]["total_rows"] == by_dept["engineering"]),
            ("audit: allow tool:query_records", audit_has(a, "tool:query_records", "allow")),
        ],
    )

    h.chat(
        "auth: research employee asks for engineering data",
        "dana",
        "Show me the engineering projects",
        lambda r, b, a: [
            (
                "zero rows (department policy, not an error)",
                b["tools"][0]["status"] == "ok" and b["table"]["total_rows"] == 0,
            ),
            (
                "SQL has both the user filter and the mandatory department='research' predicate",
                "department = 'research'" in audit_sql(a) and "department = 'engineering'" in audit_sql(a),
            ),
            (
                "audit: allow with 0 rows",
                any(e["action"] == "tool:query_records" and e["details"].get("rows") == 0 for e in a),
            ),
        ],
    )

    h.chat(
        "auth: employee sees all departments for public fields",
        "evan",
        "Generate a bar chart showing the number of projects by department.",
        lambda r, b, a: [
            ("4 departments (never less than the public view)", len(b["chart"]["data"]) == 4),
            ("counts equal ground truth", {x["department"]: x["count_records"] for x in b["table"]["rows"]} == by_dept),
        ],
    )

    rev_truth = {
        d: round(v, 2)
        for d, v in truth("select department, sum(revenue) from monthly_metrics where department='research' group by 1")
    }
    h.chat(
        "auth: employee revenue (department-scoped)",
        "dana",
        "Show monthly revenue as a line chart",
        lambda r, b, a: [
            (
                "authorized with revenue series",
                b["tools"][0]["status"] == "ok" and [s["key"] for s in b["chart"]["series"]] == ["sum_revenue"],
            ),
            (
                "limited to own department: 11 points equal research revenue",
                len(b["chart"]["data"]) == 11
                and round(sum(d["sum_revenue"] for d in b["chart"]["data"]), 2) == rev_truth["research"],
            ),
            ("SQL carries department='research'", "department = 'research'" in audit_sql(a)),
        ],
    )

    jan_jun = truth(
        "select count(*) from projects where department='engineering' and created_at between '2026-01-01' and '2026-06-30'"
    )[0][0]
    h.chat(
        "auth: reviewer, permitted date range",
        "alice",
        "Retrieve the records from my department between January and June.",
        lambda r, b, a: [
            ("authorized", b["tools"][0]["status"] == "ok"),
            ("row count equals ground truth for Jan-Jun/engineering", b["table"]["total_rows"] == jan_jun),
            (
                "all rows engineering and inside the window",
                all(
                    x["department"] == "engineering" and "2026-01-01" <= x["created_at"] <= "2026-06-30"
                    for x in b["table"]["rows"]
                ),
            ),
            ("audit: allow", audit_has(a, "tool:query_records", "allow")),
        ],
    )

    h.chat(
        "auth: reviewer exceeds permitted date range",
        "alice",
        "Retrieve the records from my department between July and November.",
        lambda r, b, a: [
            ("zero rows: window clamps the request", b["table"]["total_rows"] == 0),
            (
                "response says the range was limited",
                any("permitted window" in f for f in b["table"]["applied_filters"]),
            ),
            ("audit shows the executed (clamped) SQL", "2026-06-30" in audit_sql(a)),
        ],
    )

    h.chat(
        "auth: reviewer export to Excel (same restrictions)",
        "alice",
        "Retrieve the records from my department between January and June and export them into Excel.",
        lambda r, b, a: [
            ("export created", bool(b.get("export")) and b["export"]["format"] == "xlsx"),
            ("row_count equals ground truth", b["export"]["row_count"] == jan_jun),
            ("audit: allow tool:export_data", audit_has(a, "tool:export_data", "allow")),
        ],
    )

    h.chat(
        "auth: admin sees all departments' budgets",
        "ada",
        "What is the average budget per department?",
        lambda r, b, a: [
            ("4 departments", len(b["table"]["rows"]) == 4),
            (
                "values equal ground truth",
                {x["department"]: round(x["avg_budget"], 2) for x in b["table"]["rows"]}
                == dict(truth("select department, round(avg(budget),2) from projects group by 1")),
            ),
            ("no department predicate in SQL", "department =" not in audit_sql(a)),
        ],
    )

    h.chat(
        "auth: admin revenue across departments",
        "ada",
        "Show monthly revenue as a line chart",
        lambda r, b, a: [
            (
                "revenue series present, 11 points",
                [s["key"] for s in b["chart"]["series"]] == ["sum_revenue"] and len(b["chart"]["data"]) == 11,
            ),
            (
                "total equals all-department revenue",
                round(sum(d["sum_revenue"] for d in b["chart"]["data"]), 2)
                == round(truth("select sum(revenue) from monthly_metrics")[0][0], 2),
            ),
        ],
    )

    print("\n" + "=" * 78 + "\nRESTRICTED RESOURCES (documents)\n" + "=" * 78)
    h.chat(
        "auth: employee reads internal handbook",
        "evan",
        "What is the leave policy for staff?",
        lambda r, b, a: [
            (
                "grounded with internal citation",
                b["grounded"] and any("staff-handbook" in (c["url"] or "") for c in b["citations"]),
            ),
        ],
    )
    h.chat(
        "auth: student cannot read internal handbook",
        "sam",
        "What is the leave policy for staff?",
        lambda r, b, a: [
            ("nothing found, nothing leaked", not b["grounded"] and "26 days" not in b["answer"]),
        ],
    )
    h.chat(
        "auth: attribute-restricted PDF visible to engineering",
        "evan",
        "How many patent applications did the research department file?",
        lambda r, b, a: [
            (
                "research report cited (department in [research, engineering])",
                any("research-report" in (c["url"] or "") for c in b["citations"]),
            )
        ],
    )
    h.chat(
        "auth: attribute-restricted PDF hidden from student",
        "sam",
        "How many patent applications did the research department file?",
        lambda r, b, a: [
            ("research report NOT cited", all("research-report" not in (c["url"] or "") for c in b["citations"]))
        ],
    )
    h.chat(
        "auth: student gets public stats but not raw records",
        "sam",
        "Show all project records",
        lambda r, b, a: [
            ("denied", b["tools"][0]["status"] == "denied" and b.get("table") is None),
            ("audit deny", audit_has(a, "tool:query_records", "deny")),
        ],
    )

    h.case(
        "auth: forged token rejected",
        "anonymous",
        lambda hd: h.http.post(
            f"{h.url}/v1/chat",
            json={"message": "hi"},
            headers={"Authorization": "Bearer " + issue_token("x" * 48, user_id="evil", roles=["admin"])},
        ),
        lambda r, b, a: [("HTTP 401 (never downgraded to anonymous)", r.status_code == 401)],
    )

    h.case(
        "auth: tampered role claim rejected",
        "evan",
        lambda hd: h.http.post(
            f"{h.url}/v1/chat",
            json={"message": "hi"},
            headers={"Authorization": "Bearer " + hd["Authorization"].split()[1][:-3] + "abc"},
        ),
        lambda r, b, a: [("HTTP 401", r.status_code == 401)],
    )

    h.case(
        "integrity: demo data unchanged",
        "anonymous",
        lambda hd: h.http.get(f"{h.url}/v1/health"),
        lambda r, b, a: [
            ("projects still 120 rows", truth("select count(*) from projects")[0][0] == 120),
            ("monthly_metrics still 44 rows", truth("select count(*) from monthly_metrics")[0][0] == 44),
        ],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--json", help="write full results to this file")
    args = ap.parse_args()
    secret, admin = os.environ.get("WEBMIND_JWT_SECRET"), os.environ.get("WEBMIND_ADMIN_API_KEY")
    if not secret or not admin:
        print("set WEBMIND_JWT_SECRET and WEBMIND_ADMIN_API_KEY (same values as the server)", file=sys.stderr)
        return 2
    if not DB.exists():
        print(f"{DB} not found: run python examples/basic-website/setup_demo.py first", file=sys.stderr)
        return 2
    h = Harness(args.url, secret, admin)
    run(h)
    total = len(h.results)
    print(f"\n{'=' * 78}\n{total - h.failures}/{total} cases passed")
    if args.json:
        Path(args.json).write_text(json.dumps(h.results, indent=2, default=str), encoding="utf-8")
    return 1 if h.failures else 0


if __name__ == "__main__":
    sys.exit(main())
