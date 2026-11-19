"""Intent classification and task planning.

Two planners share one output contract (:class:`Plan`):

- ``RuleBasedPlanner`` — deterministic, works without any LLM, and is the fallback when
  an LLM plan fails validation.
- ``LLMPlanner`` — asks the configured model for a structured plan over the tools the
  principal may use. Its output is validated; it can never add capabilities.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from webmind.core.config import DatasetConfig
from webmind.core.types import Principal
from webmind.providers.base import ChatMessage, LLMProvider

Intent = Literal[
    "navigation",
    "document_qa",
    "records",
    "statistics",
    "comparison",
    "correlation",
    "visualization",
    "export",
    "report",
    "write_attempt",
    "unsupported",
]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    rationale: str | None = None


# ------------------------------------------------------------ date parsing

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS.pop("may", None)
_MONTHS["may"] = 5
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_QUARTER_WORDS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": 4}


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _quarter(y: int, q: int) -> tuple[date, date]:
    return date(y, 3 * q - 2, 1), _month_end(y, 3 * q)


def extract_periods(text: str, today: date | None = None) -> list[tuple[date, date]]:
    """Extract explicit date periods in order of appearance (deterministic)."""
    today = today or date.today()
    t = text.lower()
    year_m = re.search(r"\b(19|20)\d{2}\b", t)
    default_year = int(year_m.group(0)) if year_m else today.year
    found: list[tuple[int, tuple[date, date]]] = []

    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|until|through|-|–|and)\s*(\d{4}-\d{2}-\d{2})\b", t):
        try:
            found.append((m.start(), (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)))))
        except ValueError:
            pass
    rng = re.compile(
        rf"\b(?:between|from)?\s*({_MONTH_RE})\.?\s*(\d{{4}})?\s*(?:to|until|through|and|-|–)\s*({_MONTH_RE})\.?\s*(\d{{4}})?"
    )
    spans: list[tuple[int, int]] = []
    for m in rng.finditer(t):
        y1 = int(m.group(2) or m.group(4) or default_year)
        y2 = int(m.group(4) or y1)
        a, b = _MONTHS[m.group(1)], _MONTHS[m.group(3)]
        found.append((m.start(), (date(y1, a, 1), _month_end(y2, b))))
        spans.append(m.span())
    for m in re.finditer(r"\bq([1-4])\b(?:\s*(\d{4}))?", t):
        y = int(m.group(2) or default_year)
        found.append((m.start(), _quarter(y, int(m.group(1)))))
    for m in re.finditer(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|last)\s+quarter\b(?:\s+of\s+(\d{4}))?", t):
        y = int(m.group(2) or default_year)
        found.append((m.start(), _quarter(y, _QUARTER_WORDS[m.group(1)])))
    for m in re.finditer(r"\b(first|second)\s+half\b(?:\s+of\s+(\d{4}))?", t):
        y = int(m.group(2) or default_year)
        found.append(
            (m.start(), (date(y, 1, 1), date(y, 6, 30)) if m.group(1) == "first" else (date(y, 7, 1), date(y, 12, 31)))
        )
    for m in re.finditer(rf"\b(?:in|during|for)\s+({_MONTH_RE})\b\.?\s*(\d{{4}})?", t):
        if any(s <= m.start() < e for s, e in spans):
            continue
        y = int(m.group(2) or default_year)
        mo = _MONTHS[m.group(1)]
        found.append((m.start(), (date(y, mo, 1), _month_end(y, mo))))
    if not found:
        m = re.search(r"\b(?:in|during|for)\s+((?:19|20)\d{2})\b", t)
        if m:
            y = int(m.group(1))
            found.append((m.start(), (date(y, 1, 1), date(y, 12, 31))))
    found.sort(key=lambda x: x[0])
    out: list[tuple[date, date]] = []
    for _, p in found:
        if p not in out:
            out.append(p)
    return out


# --------------------------------------------------------- keyword tables

_WRITE = re.compile(
    r"\b(delete|drop|truncate|insert|update|modify|alter|overwrite|remove|edit|rename|grant|revoke)\b"
    r".{0,60}\b(record|records|row|rows|table|database|db|data|entry|entries|page|document|file|budget|value|permission|role)s?\b"
    r"|\b(change|set)\b(?!\s+(between|in|over|from|of)\b)\s.{0,60}\bto\b"
)
_NAV = re.compile(
    r"\b(where (can|do|could) i (find|see|get|read)|where is|which page|what page|link to|url for|how do i get to|navigate|take me to)\b"
)
_EXPORT = re.compile(r"\b(export|download|excel|xlsx|spreadsheet|csv|json file)\b")
_REPORT = re.compile(
    r"\b(report)\b.*\b(pdf|generate|create|make|produce)\b|\b(generate|create|make|produce)\b.*\breport\b"
)
_CHART = re.compile(
    r"\b(chart|graph|plot|visuali[sz]e|visuali[sz]ation|histogram|pie|trend line|line chart|bar chart)\b"
)
_CORR = re.compile(r"\b(affect|affected|impact|relationship|correlat\w*|related to|influence)\b")
_COMPARE = re.compile(r"\b(compare|comparison|versus|vs\.?|difference between|change between)\b")
_STATS = re.compile(
    r"\b(average|mean|median|total|sum|count|how many|number of|maximum|minimum|highest|lowest|statistics|stats|performance|trend)\b"
)
_RECORDS = re.compile(r"\b(show|list|retrieve|get|find|fetch|give me|display|which)\b")
_FOLLOWUP = re.compile(r"\b(it|them|these|those|this|that|same)\b")


_NAV_PREFIX = re.compile(
    r"^\s*(please\s+)?(where (can|do|could) i (find|see|get|read)( information about| info on| details on)?|where is|"
    r"which page (has|covers|explains|lists)?|what page (has|covers|explains)?|(give me |send me )?(the )?link to|"
    r"(the )?url for|how do i get to|navigate to|take me to)\s*",
    re.I,
)


def search_text(question: str) -> str:
    """Strip navigation phrasing so retrieval focuses on the topic ("eligibility requirements")."""
    cleaned = _NAV_PREFIX.sub("", question).strip(" ?.!")
    return cleaned or question


def _fn_for(t: str) -> str:
    if re.search(r"\b(average|mean)\b", t):
        return "avg"
    if re.search(r"\b(maximum|highest|max|largest)\b", t):
        return "max"
    if re.search(r"\b(minimum|lowest|min|smallest)\b", t):
        return "min"
    if re.search(r"\b(count|how many|number of)\b", t):
        return "count"
    return "sum"


def _bucket_for(t: str) -> str | None:
    for word, bucket in (
        (r"\b(monthly|per month|by month|each month|month by month|over time|trend)\b", "month"),
        (r"\b(quarterly|per quarter|by quarter|each quarter)\b", "quarter"),
        (r"\b(weekly|per week|by week)\b", "week"),
        (r"\b(daily|per day|by day)\b", "day"),
        (r"\b(yearly|annually|per year|by year)\b", "year"),
    ):
        if re.search(word, t):
            return bucket
    return None


def _word_in(word: str, t: str) -> bool:
    return re.search(rf"\b{re.escape(word.lower())}s?\b", t) is not None


class RuleBasedPlanner:
    def __init__(self, today: date | None = None) -> None:
        self.today = today

    # -- dataset & column extraction ------------------------------------------------
    def match_datasets(self, t: str, datasets: list[DatasetConfig]) -> list[DatasetConfig]:
        scored: list[tuple[int, int, DatasetConfig]] = []
        for i, d in enumerate(datasets):
            score = 0
            names = [d.id, d.id.replace("_", " "), *d.synonyms]
            score += sum(3 for n in names if _word_in(n, t))
            score += sum(2 for c in d.columns if c.aggregatable and _word_in(c.name.replace("_", " "), t))
            if score:
                scored.append((score, -i, d))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [d for _, _, d in scored]

    def metrics_for(self, t: str, ds: DatasetConfig) -> list[dict[str, Any]]:
        fn = _fn_for(t)
        cols = [c for c in ds.columns if c.aggregatable and _word_in(c.name.replace("_", " "), t)]
        if fn == "count" and not cols:
            return [{"fn": "count"}]
        if not cols:
            cols = [c for c in ds.columns if c.aggregatable][:2]
        if not cols:
            return [{"fn": "count"}]
        return [{"fn": fn, "column": c.name} for c in cols]

    def group_by_for(self, t: str, ds: DatasetConfig) -> list[str]:
        out = []
        for c in ds.columns:
            if not c.groupable:
                continue
            name = c.name.replace("_", " ")
            if re.search(rf"\b(by|per|across|each|every|grouped by|between)\s+(the\s+)?{re.escape(name)}", t) or (
                re.search(rf"\b{re.escape(name)}s\b", t) and re.search(r"\b(across|by|per|each|compare)\b", t)
            ):
                out.append(c.name)
        return out[:2]

    def filters_for(self, t: str, ds: DatasetConfig, exclude: list[str]) -> list[dict[str, Any]]:
        filters = []
        for c in ds.columns:
            if not c.values or c.name in exclude or not c.filterable:
                continue
            hits = [v for v in c.values if _word_in(v, t)]
            if len(hits) == 1:
                filters.append({"column": c.name, "op": "eq", "value": hits[0]})
            elif len(hits) > 1:
                filters.append({"column": c.name, "op": "in", "value": hits})
        return filters

    # -- planning ---------------------------------------------------------------------
    def plan(
        self, question: str, principal: Principal, datasets: list[DatasetConfig], state: dict[str, Any] | None = None
    ) -> Plan:
        state = state or {}
        t = " ".join(question.lower().split())
        last = state.get("last_query")

        if _WRITE.search(t):
            return Plan(intent="write_attempt", rationale="request asks to modify data; WebMind is read-only")

        matched = self.match_datasets(t, datasets)
        ds = matched[0] if matched else None
        if (
            ds is None
            and last
            and _FOLLOWUP.search(t)
            and (_EXPORT.search(t) or _COMPARE.search(t) or _REPORT.search(t) or _CHART.search(t))
        ):
            ds = next((d for d in datasets if d.id == last["spec"].get("dataset")), None)
        periods = extract_periods(t, self.today)

        if _NAV.search(t) and ds is None:
            return Plan(
                intent="navigation", calls=[ToolCall(tool="search_website", arguments={"query": search_text(question)})]
            )

        if ds is not None:
            base: dict[str, Any] = {"dataset": ds.id}
            prev = last["spec"] if last and last["spec"].get("dataset") == ds.id else {}
            group_by = self.group_by_for(t, ds)
            filters = self.filters_for(t, ds, exclude=group_by) or prev.get("filters", [])
            if periods:
                base["date_from"], base["date_to"] = periods[0][0].isoformat(), periods[0][1].isoformat()
            elif prev.get("date_from") or prev.get("date_to"):
                base["date_from"], base["date_to"] = prev.get("date_from"), prev.get("date_to")
            if filters:
                base["filters"] = filters

            if _EXPORT.search(t):
                fmt = (
                    "xlsx"
                    if re.search(r"\b(excel|xlsx|spreadsheet)\b", t)
                    else "json"
                    if "json" in t
                    else "pdf"
                    if "pdf" in t
                    else "csv"
                )
                return Plan(
                    intent="export", calls=[ToolCall(tool="export_data", arguments={"query": base, "format": fmt})]
                )
            if _REPORT.search(t):
                return Plan(
                    intent="report",
                    calls=[
                        ToolCall(
                            tool="generate_report",
                            arguments={"query": base, "title": f"{ds.id.replace('_', ' ').title()} report"},
                        )
                    ],
                )

            if _CORR.search(t) and len(self.metrics_for(t, ds)) >= 1:
                pair = self._correlation_pair(t, matched)
                if pair:
                    (da, ma), (db, mb) = pair
                    common = {k: v for k, v in base.items() if k in ("date_from", "date_to")}
                    return Plan(
                        intent="correlation",
                        calls=[
                            ToolCall(
                                tool="correlate_metrics",
                                arguments={
                                    "a": {
                                        "dataset": da.id,
                                        "metrics": [{"fn": "sum", "column": ma}],
                                        "time_bucket": "month",
                                        **common,
                                    },
                                    "b": {
                                        "dataset": db.id,
                                        "metrics": [{"fn": "sum", "column": mb}],
                                        "time_bucket": "month",
                                        **common,
                                    },
                                },
                            )
                        ],
                    )

            metrics = self.metrics_for(t, ds)
            if _COMPARE.search(t) and len(periods) >= 2:
                return Plan(
                    intent="comparison",
                    calls=[
                        ToolCall(
                            tool="compare_records",
                            arguments={
                                "dataset": ds.id,
                                "metric": metrics[0],
                                "group_by": group_by,
                                "period_a": {
                                    "date_from": periods[0][0].isoformat(),
                                    "date_to": periods[0][1].isoformat(),
                                },
                                "period_b": {
                                    "date_from": periods[1][0].isoformat(),
                                    "date_to": periods[1][1].isoformat(),
                                },
                                **({"filters": filters} if filters else {}),
                            },
                        )
                    ],
                )
            if _COMPARE.search(t) and len(periods) == 1 and prev.get("date_from"):
                return Plan(
                    intent="comparison",
                    calls=[
                        ToolCall(
                            tool="compare_records",
                            arguments={
                                "dataset": ds.id,
                                "metric": (prev.get("metrics") or metrics)[0] if not _STATS.search(t) else metrics[0],
                                "group_by": group_by or prev.get("group_by", []),
                                "period_a": {"date_from": prev["date_from"], "date_to": prev["date_to"]},
                                "period_b": {
                                    "date_from": periods[0][0].isoformat(),
                                    "date_to": periods[0][1].isoformat(),
                                },
                                **({"filters": filters} if filters else {}),
                            },
                        )
                    ],
                )

            bucket = _bucket_for(t)
            if _CHART.search(t):
                if "histogram" in t or "distribution" in t:
                    col = (metrics[0].get("column") if metrics else None) or next(
                        (c.name for c in ds.columns if c.aggregatable), None
                    )
                    return Plan(
                        intent="visualization",
                        calls=[
                            ToolCall(
                                tool="generate_chart",
                                arguments={
                                    "chart_type": "histogram",
                                    "histogram_dataset": ds.id,
                                    "histogram_column": col,
                                },
                            )
                        ],
                    )
                ctype = (
                    "pie"
                    if re.search(r"\b(pie|share|breakdown|proportion)\b", t)
                    else (
                        "line"
                        if re.search(r"\bline\b", t) or (bucket and not re.search(r"\bbar\b", t))
                        else "area"
                        if "area" in t
                        else "scatter"
                        if "scatter" in t
                        else "bar"
                    )
                )
                agg = {**base, "metrics": metrics[:1] if ctype == "pie" else metrics, "group_by": group_by}
                if bucket:
                    agg["time_bucket"] = bucket
                if not group_by and not bucket:
                    agg["group_by"] = [c.name for c in ds.columns if c.groupable][:1]
                return Plan(
                    intent="visualization",
                    calls=[ToolCall(tool="generate_chart", arguments={"chart_type": ctype, "query": agg})],
                )

            if _STATS.search(t) or _COMPARE.search(t) or group_by or bucket:
                agg = {**base, "metrics": metrics, "group_by": group_by}
                if bucket:
                    agg["time_bucket"] = bucket
                if _COMPARE.search(t) and not group_by and not bucket:
                    agg["group_by"] = [c.name for c in ds.columns if c.groupable][:1]
                if re.search(r"\bperformance\b", t) and not bucket and re.search(r"\bmonth", t):
                    agg["time_bucket"] = "month"
                intent: Intent = "comparison" if _COMPARE.search(t) else "statistics"
                tool = "generate_chart" if intent == "comparison" else "calculate_statistics"
                args = (
                    {"chart_type": "line" if agg.get("time_bucket") else "bar", "query": agg}
                    if tool == "generate_chart"
                    else agg
                )
                return Plan(intent=intent, calls=[ToolCall(tool=tool, arguments=args)])

            return Plan(intent="records", calls=[ToolCall(tool="query_records", arguments=base)])

        if _NAV.search(t):
            return Plan(
                intent="navigation", calls=[ToolCall(tool="search_website", arguments={"query": search_text(question)})]
            )
        return Plan(intent="document_qa", calls=[ToolCall(tool="search_documents", arguments={"query": question})])

    def _correlation_pair(
        self, t: str, matched: list[DatasetConfig]
    ) -> tuple[tuple[DatasetConfig, str], tuple[DatasetConfig, str]] | None:
        mentioned: list[tuple[int, DatasetConfig, str]] = []
        for d in matched:
            for c in d.columns:
                if not c.aggregatable:
                    continue
                m = re.search(rf"\b{re.escape(c.name.replace('_', ' '))}s?\b", t)
                if m and all(c.name != x[2] for x in mentioned):
                    mentioned.append((m.start(), d, c.name))
        mentioned.sort(key=lambda x: x[0])
        if len(mentioned) < 2:
            return None
        return (mentioned[0][1], mentioned[0][2]), (mentioned[1][1], mentioned[1][2])


# ------------------------------------------------------------------- LLM planner

PLANNER_PROMPT = """You plan tool calls for WebMind, a read-only website/data assistant.
Return JSON with: intent, calls (list of {{tool, arguments}}), rationale.
- intent is one of: navigation, document_qa, records, statistics, comparison, correlation, visualization, export, report, write_attempt, unsupported.
- Only use the tools listed below, with arguments matching their input_schema exactly.
- If the user asks to change, delete or write data, return intent "write_attempt" with no calls.
- For questions about website content use search_documents (or search_website for "where can I find").
- Never invent dataset or column names; use only those listed. Dates are ISO (YYYY-MM-DD). Today is {today}.
- Keep plans short (at most {max_calls} calls).

Datasets:
{datasets}

Tools:
{tools}

Conversation state (previous structured query, may be used for follow-ups):
{state}"""


class LLMPlanner:
    def __init__(self, llm: LLMProvider, max_calls: int) -> None:
        self.llm = llm
        self.max_calls = max_calls

    async def plan(
        self,
        question: str,
        tools: list[dict[str, Any]],
        datasets: list[DatasetConfig],
        state: dict[str, Any],
        history: list[str],
        today: date | None = None,
    ) -> Plan:
        ds_desc = json.dumps(
            [
                {
                    "id": d.id,
                    "description": d.description,
                    "date_column": d.date_column,
                    "columns": [
                        {k: v for k, v in c.model_dump().items() if v not in (None, False, [])} for c in d.columns
                    ],
                }
                for d in datasets
            ],
            indent=1,
        )
        system = PLANNER_PROMPT.format(
            today=(today or date.today()).isoformat(),
            max_calls=self.max_calls,
            datasets=ds_desc,
            tools=json.dumps(tools, indent=1),
            state=json.dumps(state.get("last_query") or {}),
        )
        convo = "\n".join(f"- {h}" for h in history[-4:])
        user = (f"Earlier questions:\n{convo}\n\n" if convo else "") + f"Question: {question}"
        plan = await self.llm.generate_structured(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)], Plan
        )
        plan.calls = plan.calls[: self.max_calls]
        return plan
