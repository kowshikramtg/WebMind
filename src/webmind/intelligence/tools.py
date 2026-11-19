"""Tool registry and built-in tools.

Every tool declares its input schema, output type, required capabilities, side-effect
class, limits and audit requirement. ``ToolRegistry.execute`` is the policy enforcement
point: capability checks, argument validation, timeouts and auditing happen there,
never inside the language model.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from webmind.core.errors import AuthorizationError, NotFoundError, QueryValidationError, WebMindError
from webmind.core.types import (
    ChartSpec,
    ExportRef,
    NavigationLink,
    Principal,
    RetrievedChunk,
    TableResult,
    ToolTrace,
)
from webmind.intelligence.analytics import (
    AggregateQuery,
    Filter,
    Metric,
    RecordQuery,
    chart_from_aggregate,
    compare_periods,
    describe,
    histogram,
    pearson,
)
from webmind.intelligence.rag import navigation_links

if TYPE_CHECKING:
    from webmind.runtime import WebMindRuntime

SideEffect = Literal["none", "internal_write"]


class ToolOutput(BaseModel):
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    navigation: list[NavigationLink] = Field(default_factory=list)
    table: TableResult | None = None
    chart: ChartSpec | None = None
    export: ExportRef | None = None
    statistics: dict[str, Any] | None = None
    note: str | None = None
    query: dict[str, Any] | None = None  # the validated query spec, for conversation state


@dataclass
class ToolContext:
    principal: Principal
    runtime: WebMindRuntime
    question: str


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    required_capabilities: ClassVar[tuple[str, ...]]
    side_effect: ClassVar[SideEffect] = "none"
    timeout_seconds: ClassVar[float] = 30.0
    audit: ClassVar[bool] = True

    @abstractmethod
    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolOutput: ...

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "required_capabilities": list(self.required_capabilities),
            "side_effect": self.side_effect,
        }


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ documents


class SearchInput(_In):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class SearchDocuments(Tool):
    name = "search_documents"
    description = "Search indexed website pages and documents the user may access; returns cited passages."
    input_model = SearchInput
    required_capabilities = ("documents:read",)
    audit = False

    async def run(self, ctx, args):
        chunks = await ctx.runtime.retriever.search(ctx.principal, args.query, k=args.top_k)
        return ToolOutput(chunks=chunks)


class SearchWebsite(Tool):
    name = "search_website"
    description = "Find which page and section of the website covers a topic; returns navigation links."
    input_model = SearchInput
    required_capabilities = ("navigate",)
    audit = False

    async def run(self, ctx, args):
        chunks = await ctx.runtime.retriever.search(ctx.principal, args.query, k=args.top_k)
        return ToolOutput(chunks=chunks, navigation=navigation_links(chunks, args.query))


class RetrieveInput(_In):
    resource_id: str = Field(min_length=1, max_length=64)


class RetrieveResource(Tool):
    name = "retrieve_resource"
    description = "Retrieve the indexed content of one resource by id (access-checked)."
    input_model = RetrieveInput
    required_capabilities = ("documents:read",)

    async def run(self, ctx, args):
        chunks = await asyncio.to_thread(ctx.runtime.resource_chunks, ctx.principal, args.resource_id)
        return ToolOutput(chunks=chunks)


# -------------------------------------------------------------------- records


class QueryRecords(Tool):
    name = "query_records"
    description = "Retrieve rows from a structured dataset with filters and a date range."
    input_model = RecordQuery
    required_capabilities = ("records:read",)

    async def run(self, ctx, args):
        table = await ctx.runtime.datasets.query_records(ctx.principal, args)
        return ToolOutput(table=table, query={"kind": "records", "spec": args.model_dump(mode="json")})


class Period(_In):
    date_from: date
    date_to: date


class CompareInput(_In):
    dataset: str
    metric: Metric
    group_by: list[str] = Field(default_factory=list, max_length=2)
    period_a: Period
    period_b: Period
    filters: list[Filter] = Field(default_factory=list, max_length=10)


class CompareRecords(Tool):
    name = "compare_records"
    description = "Compare a metric between two date periods, optionally grouped (deterministic deltas)."
    input_model = CompareInput
    required_capabilities = ("analytics",)

    async def run(self, ctx, args):
        def q(p: Period) -> AggregateQuery:
            return AggregateQuery(
                dataset=args.dataset,
                metrics=[args.metric],
                group_by=args.group_by,
                filters=args.filters,
                date_from=p.date_from,
                date_to=p.date_to,
            )

        (ta, dims), (tb, _) = await asyncio.gather(
            ctx.runtime.datasets.aggregate(ctx.principal, q(args.period_a)),
            ctx.runtime.datasets.aggregate(ctx.principal, q(args.period_b)),
        )
        rows = compare_periods(ta, tb, dims, args.metric.alias)
        cols = [*dims, "period_a", "period_b", "change", "change_pct"]
        table = TableResult(
            columns=cols,
            rows=rows,
            total_rows=len(rows),
            dataset=args.dataset,
            applied_filters=sorted(set(ta.applied_filters) | set(tb.applied_filters)),
        )
        x = dims[0] if dims else "label"
        chart_rows = rows if dims else [{"label": args.metric.alias, **r} for r in rows]
        chart = ChartSpec(
            type="bar",
            title=f"{args.metric.alias}: {args.period_a.date_from}–{args.period_a.date_to} vs "
            f"{args.period_b.date_from}–{args.period_b.date_to}",
            x_key=x,
            series=[
                {"key": "period_a", "label": f"{args.period_a.date_from} → {args.period_a.date_to}"},
                {"key": "period_b", "label": f"{args.period_b.date_from} → {args.period_b.date_to}"},
            ],
            data=chart_rows,
            source={"dataset": args.dataset},
        )
        return ToolOutput(
            table=table,
            chart=chart,
            statistics={
                "metric": args.metric.alias,
                "period_a": args.period_a.model_dump(mode="json"),
                "period_b": args.period_b.model_dump(mode="json"),
            },
            query={"kind": "aggregate", "spec": q(args.period_b).model_dump(mode="json")},
        )


class CalculateStatistics(Tool):
    name = "calculate_statistics"
    description = "Aggregate a dataset (count/sum/avg/min/max, grouped and/or bucketed by time) and describe it."
    input_model = AggregateQuery
    required_capabilities = ("analytics",)

    async def run(self, ctx, args):
        table, dims = await ctx.runtime.datasets.aggregate(ctx.principal, args)
        first = args.metrics[0].alias
        stats = {"metric": first, "across_groups": describe([r.get(first) for r in table.rows])} if dims else None
        return ToolOutput(
            table=table, statistics=stats, query={"kind": "aggregate", "spec": args.model_dump(mode="json")}
        )


class CorrelateInput(_In):
    a: AggregateQuery
    b: AggregateQuery


class CorrelateMetrics(Tool):
    name = "correlate_metrics"
    description = (
        "Align two time-bucketed metrics (possibly from different datasets) by period and compute the "
        "Pearson correlation. Correlation is not causation."
    )
    input_model = CorrelateInput
    required_capabilities = ("analytics",)

    async def run(self, ctx, args):
        for q in (args.a, args.b):
            if not q.time_bucket:
                raise QueryValidationError("correlate_metrics needs time-bucketed queries")
        (ta, _), (tb, _) = await asyncio.gather(
            ctx.runtime.datasets.aggregate(ctx.principal, args.a),
            ctx.runtime.datasets.aggregate(ctx.principal, args.b),
        )
        ma, mb = args.a.metrics[0].alias, args.b.metrics[0].alias
        mb_key = mb if mb != ma else f"{mb}_b"
        sa = {r["period"]: r.get(ma) for r in ta.rows}
        sb = {r["period"]: r.get(mb) for r in tb.rows}
        periods = sorted(set(sa) & set(sb))
        rows = [{"period": p, ma: sa[p], mb_key: sb[p]} for p in periods]
        r = pearson([sa[p] or 0 for p in periods], [sb[p] or 0 for p in periods])
        table = TableResult(
            columns=["period", ma, mb_key],
            rows=rows,
            total_rows=len(rows),
            dataset=f"{args.a.dataset}+{args.b.dataset}",
            applied_filters=sorted(set(ta.applied_filters) | set(tb.applied_filters)),
        )
        chart = ChartSpec(
            type="line",
            title=f"{ma} vs {mb}",
            x_key="period",
            series=[{"key": ma, "label": ma}, {"key": mb_key, "label": mb}],
            data=rows,
        )
        return ToolOutput(
            table=table,
            chart=chart,
            statistics={
                "pearson_r": r,
                "periods": len(periods),
                "metric_a": ma,
                "metric_b": mb,
                "caveat": "Correlation measures co-movement only; it does not establish causation.",
            },
        )


class ChartInput(_In):
    chart_type: Literal["bar", "line", "area", "pie", "scatter", "histogram"] = "bar"
    title: str | None = Field(default=None, max_length=200)
    query: AggregateQuery | None = None
    histogram_dataset: str | None = None
    histogram_column: str | None = None


class GenerateChart(Tool):
    name = "generate_chart"
    description = "Build a chart specification from an authorized aggregate query (values computed by the database)."
    input_model = ChartInput
    required_capabilities = ("analytics",)

    async def run(self, ctx, args):
        if args.chart_type == "histogram":
            if not (args.histogram_dataset and args.histogram_column):
                raise QueryValidationError("histograms need histogram_dataset and histogram_column")
            table = await ctx.runtime.datasets.query_records(
                ctx.principal,
                RecordQuery(dataset=args.histogram_dataset, columns=[args.histogram_column]),
                capability="analytics",
            )
            vals = [r[args.histogram_column] for r in table.rows]
            title = args.title or f"Distribution of {args.histogram_column}"
            return ToolOutput(
                table=table,
                chart=histogram(vals, args.histogram_column, title),
                statistics={"metric": args.histogram_column, "summary": describe(vals)},
            )
        if args.query is None:
            raise QueryValidationError("generate_chart needs a query")
        table, dims = await ctx.runtime.datasets.aggregate(ctx.principal, args.query)
        metrics = [m.alias for m in args.query.metrics]
        title = args.title or f"{', '.join(metrics)}" + (f" by {', '.join(dims)}" if dims else "")
        chart = chart_from_aggregate(table, dims, metrics, args.chart_type, title)
        return ToolOutput(
            table=table, chart=chart, query={"kind": "aggregate", "spec": args.query.model_dump(mode="json")}
        )


class ExportInput(_In):
    query: RecordQuery
    format: Literal["csv", "json", "xlsx", "pdf"] = "csv"
    title: str | None = Field(default=None, max_length=200)


class ExportData(Tool):
    name = "export_data"
    description = "Export authorized records to CSV, JSON, XLSX or PDF; returns a short-lived download link."
    input_model = ExportInput
    required_capabilities = ("records:read", "export")
    side_effect = "internal_write"  # writes only to WebMind's own export store, never to sources

    async def run(self, ctx, args):
        table = await ctx.runtime.datasets.query_records(ctx.principal, args.query, capability="export")
        title = args.title or f"{args.query.dataset} export"
        ref = await asyncio.to_thread(ctx.runtime.exports.create, ctx.principal, table, args.format, title=title)
        return ToolOutput(
            table=table, export=ref, query={"kind": "records", "spec": args.query.model_dump(mode="json")}
        )


class ReportInput(_In):
    query: RecordQuery
    title: str = Field(default="Report", max_length=200)


class GenerateReport(Tool):
    name = "generate_report"
    description = "Generate a PDF report of authorized records with a summary."
    input_model = ReportInput
    required_capabilities = ("records:read", "reports")
    side_effect = "internal_write"

    async def run(self, ctx, args):
        table = await ctx.runtime.datasets.query_records(ctx.principal, args.query)
        summary = f"{len(table.rows)} records from dataset '{table.dataset}'."
        ref = await asyncio.to_thread(
            ctx.runtime.exports.create, ctx.principal, table, "pdf", title=args.title, summary=summary
        )
        return ToolOutput(
            table=table, export=ref, query={"kind": "records", "spec": args.query.model_dump(mode="json")}
        )


BUILTIN_TOOLS: tuple[type[Tool], ...] = (
    SearchDocuments,
    SearchWebsite,
    RetrieveResource,
    QueryRecords,
    CompareRecords,
    CalculateStatistics,
    CorrelateMetrics,
    GenerateChart,
    ExportData,
    GenerateReport,
)


# ------------------------------------------------------------------- registry


@dataclass
class ToolExecution:
    trace: ToolTrace
    output: ToolOutput | None = None
    denied: bool = False
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def available_for(self, principal: Principal) -> list[Tool]:
        return [t for t in self._tools.values() if all(principal.has_capability(c) for c in t.required_capabilities)]

    async def execute(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolExecution:
        started = time.perf_counter()
        audit = ctx.runtime.audit
        tool = self._tools.get(name)
        if tool is None:
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="error", message="unknown tool"), error="unknown tool"
            )
        missing = [c for c in tool.required_capabilities if not ctx.principal.has_capability(c)]
        if missing:
            audit.record(
                ctx.principal,
                f"tool:{name}",
                decision="deny",
                reason=f"missing capabilities: {', '.join(missing)}",
                details={"arguments": arguments},
            )
            msg = "You don't have permission to perform this operation."
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="denied", message=msg), denied=True, error=msg
            )
        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            msg = f"invalid arguments: {exc.errors(include_url=False)[:3]}"
            return ToolExecution(ToolTrace(tool=name, arguments=arguments, status="error", message=msg), error=msg)
        try:
            output = await asyncio.wait_for(tool.run(ctx, args), timeout=tool.timeout_seconds)
        except AuthorizationError as exc:
            audit.record(
                ctx.principal,
                f"tool:{name}",
                decision="deny",
                target=exc.resource,
                reason=str(exc),
                details={"arguments": arguments},
            )
            msg = "You don't have permission to access that information."
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="denied", message=msg), denied=True, error=msg
            )
        except (QueryValidationError, NotFoundError) as exc:
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="error", message=str(exc)), error=str(exc)
            )
        except asyncio.TimeoutError:
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="error", message="timed out"),
                error="The operation timed out.",
            )
        except WebMindError as exc:
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="error", message=str(exc)), error=str(exc)
            )
        except Exception as exc:  # unexpected failures never leak internals to the caller
            import logging

            logging.getLogger("webmind.tools").exception("tool %s failed", name)
            return ToolExecution(
                ToolTrace(tool=name, arguments=arguments, status="error", message="internal error"),
                error=f"internal error ({type(exc).__name__})",
            )
        ms = round((time.perf_counter() - started) * 1000, 1)
        if tool.audit:
            details: dict[str, Any] = {"arguments": arguments, "duration_ms": ms}
            if output.table is not None:
                details["rows"] = len(output.table.rows)
                details["applied_filters"] = output.table.applied_filters
            audit.record(
                ctx.principal,
                f"tool:{name}",
                decision="allow",
                details=details,
                target=getattr(args, "dataset", None) or (output.table.dataset if output.table else None),
            )
        return ToolExecution(ToolTrace(tool=name, arguments=arguments, status="ok", duration_ms=ms), output=output)


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for cls in BUILTIN_TOOLS:
        reg.register(cls())
    return reg
