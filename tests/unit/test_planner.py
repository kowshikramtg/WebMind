from datetime import date

import pytest

from webmind.core.config import config_from_dict
from webmind.core.types import Principal
from webmind.intelligence.planner import RuleBasedPlanner, extract_periods, search_text

TODAY = date(2026, 11, 19)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("between January and June", [(date(2026, 1, 1), date(2026, 6, 30))]),
        ("from Feb 2025 to Apr 2025", [(date(2025, 2, 1), date(2025, 4, 30))]),
        ("revenue for Q1 2026", [(date(2026, 1, 1), date(2026, 3, 31))]),
        ("first quarter", [(date(2026, 1, 1), date(2026, 3, 31))]),
        ("the second half of 2025", [(date(2025, 7, 1), date(2025, 12, 31))]),
        ("in March", [(date(2026, 3, 1), date(2026, 3, 31))]),
        ("2026-02-01 to 2026-02-15", [(date(2026, 2, 1), date(2026, 2, 15))]),
        ("Compare Q1 with Q2", [(date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 6, 30))]),
        ("during 2024", [(date(2024, 1, 1), date(2024, 12, 31))]),
        ("no dates here", []),
    ],
)
def test_extract_periods(text, expected):
    assert extract_periods(text, TODAY) == expected


@pytest.fixture
def datasets():
    cfg = config_from_dict(
        {
            "datasets": [
                {
                    "id": "projects",
                    "synonyms": ["records", "project"],
                    "connection": "sqlite:///x.db",
                    "table": "projects",
                    "date_column": "created_at",
                    "columns": [
                        {"name": "department", "groupable": True, "values": ["engineering", "research"]},
                        {"name": "created_at", "type": "date"},
                        {"name": "budget", "type": "number", "aggregatable": True},
                    ],
                },
                {
                    "id": "monthly_metrics",
                    "synonyms": ["performance"],
                    "connection": "sqlite:///x.db",
                    "table": "m",
                    "date_column": "month",
                    "columns": [
                        {"name": "month", "type": "date"},
                        {"name": "department", "groupable": True},
                        {"name": "registrations", "type": "integer", "aggregatable": True},
                        {"name": "revenue", "type": "number", "aggregatable": True},
                    ],
                },
            ]
        }
    )
    return cfg.datasets


P = Principal(tenant_id="default")


def plan(q, datasets, state=None):
    return RuleBasedPlanner(today=TODAY).plan(q, P, datasets, state)


def test_document_and_navigation_intents(datasets):
    assert plan("Explain the services offered", datasets).calls[0].tool == "search_documents"
    p = plan("Where can I find the eligibility requirements?", datasets)
    assert p.intent == "navigation"
    assert p.calls[0].arguments["query"] == "the eligibility requirements"
    assert search_text("which page covers fees?") == "fees"


def test_records_with_dates_and_filters(datasets):
    p = plan("Retrieve the engineering records between January and June", datasets)
    assert p.intent == "records"
    args = p.calls[0].arguments
    assert args["dataset"] == "projects"
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-06-30"
    assert args["filters"] == [{"column": "department", "op": "eq", "value": "engineering"}]


def test_chart_and_grouping(datasets):
    p = plan("Generate a line chart of monthly registrations by department", datasets)
    assert p.intent == "visualization"
    q = p.calls[0].arguments["query"]
    assert p.calls[0].arguments["chart_type"] == "line"
    assert q["time_bucket"] == "month" and q["group_by"] == ["department"]
    assert q["metrics"] == [{"fn": "sum", "column": "registrations"}]


def test_statistics_function_detection(datasets):
    p = plan("What is the average budget per department?", datasets)
    assert p.calls[0].tool == "calculate_statistics"
    assert p.calls[0].arguments["metrics"] == [{"fn": "avg", "column": "budget"}]
    assert p.calls[0].arguments["group_by"] == ["department"]


def test_correlation(datasets):
    p = plan("How did the increase in registrations affect monthly revenue?", datasets)
    assert p.intent == "correlation"
    args = p.calls[0].arguments
    assert args["a"]["metrics"][0]["column"] == "registrations"
    assert args["b"]["metrics"][0]["column"] == "revenue"


def test_follow_up_uses_conversation_state(datasets):
    state = {
        "last_query": {
            "kind": "aggregate",
            "spec": {
                "dataset": "monthly_metrics",
                "metrics": [{"fn": "sum", "column": "revenue"}],
                "group_by": [],
                "date_from": "2026-01-01",
                "date_to": "2026-03-31",
            },
        }
    }
    p = plan("Compare it with the second quarter", datasets, state)
    assert p.intent == "comparison"
    args = p.calls[0].arguments
    assert args["period_a"] == {"date_from": "2026-01-01", "date_to": "2026-03-31"}
    assert args["period_b"] == {"date_from": "2026-04-01", "date_to": "2026-06-30"}
    assert args["metric"] == {"fn": "sum", "column": "revenue"}

    p = plan("Export these into Excel", datasets, state)
    assert p.intent == "export"
    assert p.calls[0].arguments["format"] == "xlsx"
    assert p.calls[0].arguments["query"]["dataset"] == "monthly_metrics"


@pytest.mark.parametrize(
    "q",
    [
        "Delete the records from the projects table",
        "update the budget value of project 5",
        "please drop the database",
        "change the budget of project 4 to 0",
    ],
)
def test_write_attempts_are_detected(q, datasets):
    assert plan(q, datasets).intent == "write_attempt"


@pytest.mark.parametrize("q", ["What is the change between Q1 and Q2 revenue?", "How do fees change over time?"])
def test_not_write_attempts(q, datasets):
    assert plan(q, datasets).intent != "write_attempt"
