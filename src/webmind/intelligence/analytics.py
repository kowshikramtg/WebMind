"""Controlled structured-data access ("semantic layer" Text-to-SQL).

Language models never write SQL here. A model (or the deterministic planner) proposes a
typed query *specification*; this module validates it against the dataset allowlist,
appends mandatory policy filters (tenant, row-level, date window), compiles it to a
parameterized ``SELECT`` with SQLAlchemy Core, and executes it on a read-only connection
with a timeout and row limit. All numbers come from the database, never from the model.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import threading
import time
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    cast,
    create_engine,
    event,
    false,
    func,
    literal,
    select,
    text,
    true,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.sql import ColumnElement, Select

from webmind.core.config import DatasetConfig, WebMindConfig, parse_iso_date
from webmind.core.errors import AuthorizationError, ConfigurationError, QueryValidationError
from webmind.core.types import ChartSeries, ChartSpec, Principal, TableResult
from webmind.security.policy import PolicyEngine

# ----------------------------------------------------------------- query specs

FilterOp = Literal["eq", "neq", "in", "gt", "gte", "lt", "lte", "contains"]
MetricFn = Literal["count", "sum", "avg", "min", "max"]
TimeBucket = Literal["day", "week", "month", "quarter", "year"]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_Spec):
    column: str = Field(max_length=63)
    op: FilterOp = "eq"
    value: str | int | float | bool | list[str | int | float] | None = None


class Metric(_Spec):
    fn: MetricFn
    column: str | None = Field(default=None, max_length=63)

    @property
    def alias(self) -> str:
        return f"{self.fn}_{self.column or 'records'}"


class RecordQuery(_Spec):
    dataset: str = Field(max_length=64)
    columns: list[str] | None = Field(default=None, max_length=50)
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    date_from: date | None = None
    date_to: date | None = None
    order_by: str | None = None
    descending: bool = False
    limit: int | None = Field(default=None, ge=1, le=100_000)


class AggregateQuery(_Spec):
    dataset: str = Field(max_length=64)
    metrics: list[Metric] = Field(min_length=1, max_length=8)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    time_bucket: TimeBucket | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    date_from: date | None = None
    date_to: date | None = None
    order_by: str | None = None
    descending: bool = False
    limit: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("metrics")
    @classmethod
    def _unique(cls, v: list[Metric]) -> list[Metric]:
        aliases = [m.alias for m in v]
        if len(set(aliases)) != len(aliases):
            raise ValueError("duplicate metrics")
        return v


# ------------------------------------------------------------------- engines

_TYPES = {
    "string": String,
    "integer": Integer,
    "number": Float,
    "date": Date,
    "datetime": DateTime,
    "boolean": Boolean,
}


def _read_only_engine(url: str, base_dir: str | Path) -> Engine:
    u = make_url(url)
    backend = u.get_backend_name()
    if backend == "sqlite":
        db = u.database or ""
        if not db or db == ":memory:":
            raise ConfigurationError("dataset sqlite connections must point at a database file")
        p = Path(db)
        if not p.is_absolute():
            p = (Path(base_dir) / p).resolve()
        # mode=ro: the SQLite library itself refuses writes; query_only is a second guard.
        engine = create_engine(
            f"sqlite:///file:{p.as_posix()}?mode=ro&uri=true",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _ro(dbapi_conn, _rec):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA query_only = ON")

        return engine
    if backend == "postgresql":
        engine = create_engine(url, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _ro_pg(dbapi_conn, _rec):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.close()
            # Commit so a later pool reset (ROLLBACK) cannot revert the session setting.
            dbapi_conn.commit()

        return engine
    if backend in ("mysql", "mariadb"):
        engine = create_engine(url, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _ro_my(dbapi_conn, _rec):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.close()
            dbapi_conn.commit()

        return engine
    raise ConfigurationError(f"unsupported dataset backend '{backend}' (sqlite, postgresql, mysql)")


def _bucket(col: ColumnElement, bucket: TimeBucket, dialect: str) -> ColumnElement:
    if dialect == "sqlite":
        fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m", "year": "%Y"}
        if bucket == "quarter":
            return (
                func.strftime("%Y", col)
                + literal("-Q")
                + cast((cast(func.strftime("%m", col), Integer) + 2) / 3, String)
            )
        return func.strftime(fmt[bucket], col)
    if dialect == "postgresql":
        fmt = {"day": "YYYY-MM-DD", "week": 'IYYY-"W"IW', "month": "YYYY-MM", "quarter": 'YYYY-"Q"Q', "year": "YYYY"}
        return func.to_char(col, fmt[bucket])
    fmt = {"day": "%Y-%m-%d", "week": "%x-W%v", "month": "%Y-%m", "year": "%Y"}
    if bucket == "quarter":
        return func.concat(func.year(col), "-Q", func.quarter(col))
    return func.date_format(col, fmt[bucket])


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class DatasetRegistry:
    """Owns dataset definitions, read-only engines, query compilation and execution."""

    def __init__(self, cfg: WebMindConfig, policy: PolicyEngine) -> None:
        self.cfg = cfg
        self.policy = policy
        self._engines: dict[str, Engine] = {}
        self._tables: dict[str, Table] = {}
        self._lock = threading.Lock()

    # -- metadata -----------------------------------------------------------------
    def get(self, dataset_id: str) -> DatasetConfig:
        try:
            return self.cfg.dataset(dataset_id)
        except KeyError as exc:
            raise QueryValidationError(f"unknown dataset '{dataset_id}'") from exc

    def accessible(self, principal: Principal) -> list[DatasetConfig]:
        return [
            d for d in self.cfg.datasets if self.policy.can_access(principal, d.access, tenant_id=self.cfg.tenant_of(d))
        ]

    def _engine(self, ds: DatasetConfig) -> Engine:
        with self._lock:
            if ds.connection not in self._engines:
                self._engines[ds.connection] = _read_only_engine(ds.connection, self.cfg.base_dir)
            return self._engines[ds.connection]

    def _table(self, ds: DatasetConfig) -> Table:
        with self._lock:
            if ds.id not in self._tables:
                schema, _, name = ds.table.rpartition(".")
                self._tables[ds.id] = Table(
                    name,
                    MetaData(),
                    *[Column(c.name, _TYPES[c.type]()) for c in ds.columns],
                    schema=schema or None,
                )
            return self._tables[ds.id]

    # -- authorization & scoping --------------------------------------------------
    def authorize(self, principal: Principal, ds: DatasetConfig, capability: str) -> None:
        self.policy.require_capability(principal, capability)
        decision = self.policy.can_access(principal, ds.access, tenant_id=self.cfg.tenant_of(ds))
        if not decision:
            raise AuthorizationError(
                f"access to dataset '{ds.id}' denied ({decision.reason})", action=capability, resource=ds.id
            )

    def _scope(
        self,
        ds: DatasetConfig,
        table: Table,
        principal: Principal,
        date_from: date | None,
        date_to: date | None,
    ) -> tuple[list[ColumnElement], list[str]]:
        """Mandatory policy filters + the effective date range. Never optional."""
        conds: list[ColumnElement] = []
        notes: list[str] = []
        if ds.tenant_column:
            conds.append(table.c[ds.tenant_column] == principal.tenant_id)
            notes.append(f"tenant = {principal.tenant_id}")
        for rf in ds.row_filters:
            if self.policy.exempt(principal, rf.unless_roles):
                continue
            val = principal.attributes.get(rf.equals_attribute)
            if val is None or val == [] or val == "":
                conds.append(false())
                notes.append(f"{rf.column}: no '{rf.equals_attribute}' attribute → no rows")
            elif isinstance(val, list):
                conds.append(table.c[rf.column].in_([str(v) for v in val]))
                notes.append(f"{rf.column} in your {rf.equals_attribute}s")
            else:
                conds.append(table.c[rf.column] == str(val))
                notes.append(f"{rf.column} = {val} (your {rf.equals_attribute})")

        eff_from, eff_to = date_from, date_to
        dw = ds.date_window
        if dw and not self.policy.exempt(principal, dw.unless_roles):
            for attr, is_from in ((dw.from_attribute, True), (dw.to_attribute, False)):
                if not attr:
                    continue
                bound = parse_iso_date(principal.attributes.get(attr))
                if bound is None:
                    conds.append(false())
                    notes.append(f"date window: missing '{attr}' attribute → no rows")
                    continue
                if is_from:
                    eff_from = max(eff_from, bound) if eff_from else bound
                else:
                    eff_to = min(eff_to, bound) if eff_to else bound
            if eff_from != date_from or eff_to != date_to:
                notes.append(f"date range limited to your permitted window ({eff_from} → {eff_to})")
        if (eff_from or eff_to) and not ds.date_column:
            raise QueryValidationError(f"dataset '{ds.id}' has no date column; date ranges are not supported")
        if ds.date_column:
            col = table.c[ds.date_column]
            if eff_from:
                conds.append(col >= eff_from)
            if eff_to:
                if next(c for c in ds.columns if c.name == ds.date_column).type == "datetime":
                    conds.append(func.date(col) <= eff_to)
                else:
                    conds.append(col <= eff_to)
        if eff_from or eff_to:
            notes.append(f"{ds.date_column} between {eff_from or '…'} and {eff_to or '…'}")
        return conds, notes

    def _user_filters(self, ds: DatasetConfig, table: Table, filters: Sequence[Filter]) -> tuple[list, list[str]]:
        conds: list[ColumnElement] = []
        notes: list[str] = []
        for f in filters:
            col_cfg = ds.column(f.column)
            if col_cfg is None or not col_cfg.filterable:
                raise QueryValidationError(f"column '{f.column}' is not filterable in dataset '{ds.id}'")
            col = table.c[f.column]

            def coerce(v: Any, ctype: str = col_cfg.type, column: str = f.column) -> Any:
                if v is None:
                    raise QueryValidationError(f"filter on '{column}' needs a value")
                try:
                    if ctype in ("date", "datetime"):
                        d = parse_iso_date(v)
                        if d is None:
                            raise ValueError
                        return d
                    if ctype == "integer":
                        return int(v)
                    if ctype == "number":
                        return float(v)
                    if ctype == "boolean":
                        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
                    return str(v)
                except (TypeError, ValueError) as exc:
                    raise QueryValidationError(f"invalid value for '{column}': {v!r}") from exc

            if f.op == "in":
                vals = f.value if isinstance(f.value, list) else [f.value]
                if not 1 <= len(vals) <= 50:
                    raise QueryValidationError("'in' filters accept 1–50 values")
                conds.append(col.in_([coerce(v) for v in vals]))
            elif f.op == "contains":
                if col_cfg.type != "string":
                    raise QueryValidationError("'contains' only applies to text columns")
                conds.append(col.contains(str(f.value), autoescape=True))
            else:
                if isinstance(f.value, list):
                    raise QueryValidationError(f"operator '{f.op}' takes a single value")
                v = coerce(f.value)
                ops = {"eq": col == v, "neq": col != v, "gt": col > v, "gte": col >= v, "lt": col < v, "lte": col <= v}
                conds.append(ops[f.op])
            notes.append(f"{f.column} {f.op} {f.value}")
        return conds, notes

    # -- compilation --------------------------------------------------------------
    def compile_records(self, principal: Principal, q: RecordQuery) -> tuple[Select, DatasetConfig, int, list[str]]:
        ds = self.get(q.dataset)
        table = self._table(ds)
        cols = q.columns or [c.name for c in ds.columns]
        for c in cols:
            if ds.column(c) is None:
                raise QueryValidationError(f"unknown column '{c}' in dataset '{ds.id}'")
        if ds.tenant_column and ds.tenant_column in cols and q.columns is None:
            cols = [c for c in cols if c != ds.tenant_column]
        scope, notes = self._scope(ds, table, principal, q.date_from, q.date_to)
        user, unotes = self._user_filters(ds, table, q.filters)
        limit = min(q.limit or ds.max_rows, ds.max_rows)
        stmt = select(*[table.c[c] for c in cols]).where(and_(true(), *scope, *user))
        order = q.order_by or ds.date_column
        if order:
            if ds.column(order) is None:
                raise QueryValidationError(f"cannot order by unknown column '{order}'")
            stmt = stmt.order_by(table.c[order].desc() if q.descending else table.c[order].asc())
        return stmt.limit(limit + 1), ds, limit, notes + unotes

    def compile_aggregate(
        self, principal: Principal, q: AggregateQuery
    ) -> tuple[Select, DatasetConfig, int, list[str], list[str]]:
        ds = self.get(q.dataset)
        table = self._table(ds)
        dialect = self._engine(ds).dialect.name
        dims: list[ColumnElement] = []
        dim_names: list[str] = []
        if q.time_bucket:
            if not ds.date_column:
                raise QueryValidationError(f"dataset '{ds.id}' has no date column for time buckets")
            dims.append(_bucket(table.c[ds.date_column], q.time_bucket, dialect).label("period"))
            dim_names.append("period")
        for g in q.group_by:
            cc = ds.column(g)
            if cc is None or not cc.groupable:
                raise QueryValidationError(f"column '{g}' cannot be used for grouping in '{ds.id}'")
            dims.append(table.c[g].label(g))
            dim_names.append(g)
        measures: list[ColumnElement] = []
        for m in q.metrics:
            if m.column is None:
                if m.fn != "count":
                    raise QueryValidationError(f"metric '{m.fn}' needs a column")
                measures.append(func.count().label(m.alias))
                continue
            cc = ds.column(m.column)
            if cc is None:
                raise QueryValidationError(f"unknown column '{m.column}' in dataset '{ds.id}'")
            if m.fn != "count" and not cc.aggregatable:
                raise QueryValidationError(f"column '{m.column}' cannot be aggregated with {m.fn}")
            fn = {"count": func.count, "sum": func.sum, "avg": func.avg, "min": func.min, "max": func.max}[m.fn]
            measures.append(fn(table.c[m.column]).label(m.alias))
        scope, notes = self._scope(ds, table, principal, q.date_from, q.date_to)
        user, unotes = self._user_filters(ds, table, q.filters)
        limit = min(q.limit or ds.max_rows, ds.max_rows)
        stmt = select(*dims, *measures).where(and_(true(), *scope, *user))
        if dims:
            stmt = stmt.group_by(*[text(n) if n == "period" else table.c[n] for n in dim_names])
        aliases = dim_names + [m.alias for m in q.metrics]
        if q.order_by:
            if q.order_by not in aliases:
                raise QueryValidationError(f"cannot order by '{q.order_by}'")
            col = text(q.order_by)
            stmt = stmt.order_by(col.desc() if q.descending else col)
        elif dims:
            stmt = stmt.order_by(*[text(n) for n in dim_names])
        return stmt.limit(limit + 1), ds, limit, notes + unotes, dim_names

    # -- execution ------------------------------------------------------------------
    def _execute(self, ds: DatasetConfig, stmt: Select) -> list[dict[str, Any]]:
        engine = self._engine(ds)
        deadline = time.monotonic() + ds.timeout_seconds
        with engine.connect() as conn:
            dialect = engine.dialect.name
            raw = conn.connection.dbapi_connection
            if dialect == "sqlite":
                raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)  # type: ignore[union-attr]
            elif dialect == "postgresql":
                conn.execute(text(f"SET LOCAL statement_timeout = {int(ds.timeout_seconds * 1000)}"))
            elif dialect in ("mysql", "mariadb"):
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {int(ds.timeout_seconds * 1000)}"))
            try:
                rows = conn.execute(stmt).mappings().all()
            finally:
                if dialect == "sqlite":
                    raw.set_progress_handler(None, 0)  # type: ignore[union-attr]
                conn.rollback()
        return [{k: _jsonable(v) for k, v in r.items()} for r in rows]

    async def query_records(
        self, principal: Principal, q: RecordQuery, *, capability: str = "records:read"
    ) -> TableResult:
        ds = self.get(q.dataset)
        self.authorize(principal, ds, capability)
        stmt, ds, limit, notes = self.compile_records(principal, q)
        rows = await asyncio.to_thread(self._execute, ds, stmt)
        truncated = len(rows) > limit
        rows = rows[:limit]
        return TableResult(
            columns=list(rows[0].keys()) if rows else [c.name for c in stmt.selected_columns],
            rows=rows,
            total_rows=len(rows),
            truncated=truncated,
            dataset=ds.id,
            applied_filters=notes,
        )

    async def aggregate(self, principal: Principal, q: AggregateQuery) -> tuple[TableResult, list[str]]:
        ds = self.get(q.dataset)
        self.authorize(principal, ds, "analytics")
        stmt, ds, limit, notes, dims = self.compile_aggregate(principal, q)
        rows = await asyncio.to_thread(self._execute, ds, stmt)
        truncated = len(rows) > limit
        rows = rows[:limit]
        cols = dims + [m.alias for m in q.metrics]
        return TableResult(
            columns=cols, rows=rows, total_rows=len(rows), truncated=truncated, dataset=ds.id, applied_filters=notes
        ), dims

    def dispose(self) -> None:
        for e in self._engines.values():
            e.dispose()


# --------------------------------------------------------------- statistics


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "sum": round(sum(vals), 6),
        "mean": round(statistics.fmean(vals), 6),
        "median": round(statistics.median(vals), 6),
        "stdev": round(statistics.stdev(vals), 6) if len(vals) > 1 else 0.0,
        "min": min(vals),
        "max": max(vals),
    }


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    try:
        return round(statistics.correlation([float(x) for x in xs], [float(y) for y in ys]), 4)
    except statistics.StatisticsError:
        return None


def compare_periods(a: TableResult, b: TableResult, dims: list[str], metric: str) -> list[dict[str, Any]]:
    """Join two aggregate results on their dimensions and compute deltas deterministically."""

    def key(row: dict[str, Any]) -> tuple:
        return tuple(row.get(d) for d in dims)

    left = {key(r): r.get(metric) for r in a.rows}
    right = {key(r): r.get(metric) for r in b.rows}
    out = []
    for k in sorted(set(left) | set(right), key=lambda t: tuple(str(x) for x in t)):
        va, vb = left.get(k), right.get(k)
        row = {d: v for d, v in zip(dims, k, strict=True)}
        row.update({"period_a": va, "period_b": vb})
        if va is not None and vb is not None:
            row["change"] = round(float(vb) - float(va), 6)
            row["change_pct"] = round((float(vb) - float(va)) / float(va) * 100, 2) if float(va) != 0 else None
        out.append(row)
    return out


# --------------------------------------------------------------------- charts


def _label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def chart_from_aggregate(
    table: TableResult, dims: list[str], metrics: list[str], chart_type: str, title: str
) -> ChartSpec:
    rows = table.rows
    if chart_type == "pie" and dims:
        return ChartSpec(
            type="pie",
            title=title,
            x_key=dims[-1],
            series=[ChartSeries(key=metrics[0], label=_label(metrics[0]))],
            data=[{dims[-1]: str(r.get(dims[-1])), metrics[0]: r.get(metrics[0])} for r in rows],
            source={"dataset": table.dataset, "filters": table.applied_filters},
        )
    if len(dims) >= 2:
        # Pivot: first dim on the x axis, one series per value of the second dim.
        x, s = dims[0], dims[1]
        metric = metrics[0]
        series_keys: list[str] = []
        pivot: dict[Any, dict[str, Any]] = {}
        for r in rows:
            sk = str(r.get(s))
            if sk not in series_keys:
                series_keys.append(sk)
            pivot.setdefault(r.get(x), {x: r.get(x)})[sk] = r.get(metric)
        data = [pivot[k] for k in sorted(pivot, key=lambda v: str(v))]
        return ChartSpec(
            type=chart_type if chart_type != "pie" else "bar",
            title=title,
            x_key=x,
            x_label=_label(x),
            y_label=_label(metric),
            series=[ChartSeries(key=k, label=k) for k in series_keys],
            data=data,
            source={"dataset": table.dataset, "filters": table.applied_filters},
        )
    x = dims[0] if dims else "label"
    data = rows if dims else [{"label": "total", **r} for r in rows]
    return ChartSpec(
        type=chart_type if chart_type != "pie" else "bar",
        title=title,
        x_key=x,
        x_label=_label(x),
        y_label=_label(metrics[0]) if len(metrics) == 1 else None,
        series=[ChartSeries(key=m, label=_label(m)) for m in metrics],
        data=data,
        source={"dataset": table.dataset, "filters": table.applied_filters},
    )


def histogram(values: Sequence[float], column: str, title: str, bins: int | None = None) -> ChartSpec:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return ChartSpec(type="histogram", title=title, x_key="bin", series=[], data=[])
    bins = bins or max(1, min(30, math.ceil(math.log2(len(vals)) + 1)))  # Sturges' rule
    lo, hi = vals[0], vals[-1]
    width = (hi - lo) / bins or 1.0
    counts = [0] * bins
    for v in vals:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    data = [{"bin": f"{lo + i * width:.4g}–{lo + (i + 1) * width:.4g}", "count": c} for i, c in enumerate(counts)]
    return ChartSpec(
        type="histogram",
        title=title,
        x_key="bin",
        x_label=_label(column),
        y_label="Count",
        series=[ChartSeries(key="count", label="Count")],
        data=data,
    )
