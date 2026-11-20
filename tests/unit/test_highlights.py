from webmind.core.types import TableResult
from webmind.intelligence.orchestrator import _highlights


def _t(columns, rows):
    return TableResult(columns=columns, rows=rows, total_rows=len(rows))


def test_highlights_are_computed_deterministically_from_rows():
    t = _t(
        ["department", "count_records"],
        [
            {"department": d, "count_records": n}
            for d, n in [("admissions", 23), ("engineering", 24), ("finance", 42), ("research", 31)]
        ],
    )
    h = _highlights(t)["count_records"]
    assert h["highest"] == {"group": "finance", "value": 42}
    assert h["lowest"] == {"group": "admissions", "value": 23}
    assert h["total_of_all_groups"] == 120


def test_highlights_skip_averages_totals_and_raw_records():
    avg = _t(
        ["department", "avg_budget"], [{"department": "a", "avg_budget": 1.0}, {"department": "b", "avg_budget": 3.0}]
    )
    assert "total_of_all_groups" not in _highlights(avg)["avg_budget"]  # a total of averages is meaningless
    raw = _t(["id", "name", "budget"], [{"id": 1, "name": "x", "budget": 5.0}, {"id": 2, "name": "y", "budget": 9.0}])
    assert _highlights(raw) == {}  # raw rows: nothing to rank
    assert _highlights(_t(["department", "count_records"], [{"department": "a", "count_records": 1}])) == {}
    assert _highlights(_t(["count_records"], [])) == {}


def test_multi_dimension_labels():
    t = _t(
        ["period", "department", "sum_revenue"],
        [
            {"period": "2026-01", "department": "a", "sum_revenue": 5},
            {"period": "2026-02", "department": "b", "sum_revenue": 9},
        ],
    )
    assert _highlights(t)["sum_revenue"]["highest"]["group"] == "2026-02 / b"


def test_access_limits_note_lists_only_policy_restrictions():
    from webmind.intelligence.orchestrator import _access_limits

    filters = [
        "department = engineering (your department)",
        "date range limited to your permitted window (2026-01-01 → 2026-06-30)",
        "status eq active",  # the user's own filter: not an access limit
        "tenant = default",
    ]
    note = _access_limits(filters)
    assert "your department" in note and "permitted window" in note
    assert "status" not in note and "tenant" not in note
    assert _access_limits(["status eq active"]) == ""


def test_correlation_rule_only_when_a_correlation_is_present():
    from types import SimpleNamespace

    from webmind.intelligence.orchestrator import _data_prompt

    assert "correlation" not in _data_prompt(SimpleNamespace(statistics=None)).lower()
    assert "causation" in _data_prompt(SimpleNamespace(statistics={"pearson_r": 0.8})).lower()
