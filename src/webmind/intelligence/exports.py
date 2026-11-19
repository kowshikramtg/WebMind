"""Export generation (CSV / JSON / XLSX / PDF) with per-user, expiring downloads.

Exports only ever contain rows returned by the authorized query path; the export
service re-checks ownership on download and deletes expired files.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select

from webmind.core.config import WebMindConfig
from webmind.core.errors import NotFoundError, ProviderNotInstalledError
from webmind.core.types import ExportRef, Principal, TableResult
from webmind.storage.database import Database
from webmind.storage.models import ExportJob, new_id
from webmind.storage.object_store import ObjectStore

ExportFormat = Literal["csv", "json", "xlsx", "pdf"]
MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula injection (CSV/XLSX)."""
    if isinstance(value, str) and value.startswith(_FORMULA):
        return "'" + value
    return value


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "export"


def render(table: TableResult, fmt: ExportFormat, title: str) -> bytes:
    cols = table.columns
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in table.rows:
            w.writerow([safe_cell(r.get(c)) for c in cols])
        return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM for Excel
    if fmt == "json":
        payload = {
            "title": title,
            "dataset": table.dataset,
            "applied_filters": table.applied_filters,
            "row_count": len(table.rows),
            "truncated": table.truncated,
            "rows": table.rows,
        }
        return json.dumps(payload, indent=2, default=str).encode("utf-8")
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ProviderNotInstalledError("xlsx export", "xlsx") from exc
        wb = Workbook()
        ws = wb.active
        ws.title = "data"
        ws.append(cols)
        for r in table.rows:
            row = []
            for c in cols:
                v = safe_cell(r.get(c))
                row.append(v)
            ws.append(row)
        from openpyxl.styles import Font

        for cell in ws[1]:
            cell.font = Font(bold=True)
        meta = wb.create_sheet("about")
        meta.append(["title", title])
        meta.append(["dataset", table.dataset or ""])
        meta.append(["generated_at", datetime.now(timezone.utc).isoformat()])
        meta.append(["rows", len(table.rows)])
        meta.append(["truncated", str(table.truncated)])
        for f in table.applied_filters:
            meta.append(["filter", safe_cell(f)])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    if fmt == "pdf":
        return render_pdf(table, title)
    raise ValueError(f"unsupported export format: {fmt}")


def render_pdf(table: TableResult, title: str, summary: str | None = None) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ProviderNotInstalledError("pdf export", "reports") from exc
    from xml.sax.saxutils import escape

    out = io.BytesIO()
    size = landscape(A4) if len(table.columns) > 5 else A4
    doc = SimpleDocTemplate(out, pagesize=size, title=title)
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph(escape(title), styles["Title"])]
    meta = f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {len(table.rows)} rows"
    if table.dataset:
        meta += f" · dataset: {escape(table.dataset)}"
    story.append(Paragraph(meta, styles["Normal"]))
    for f in table.applied_filters:
        story.append(Paragraph("Filter: " + escape(f), styles["Normal"]))
    if summary:
        story += [Spacer(1, 8), Paragraph(escape(summary), styles["BodyText"])]
    story.append(Spacer(1, 12))
    cell_style = styles["BodyText"]
    cell_style.fontSize = 8
    cell_style.leading = 10
    data = [[Paragraph(f"<b>{escape(c)}</b>", cell_style) for c in table.columns]]
    for r in table.rows:
        data.append([Paragraph(escape(str(r.get(c, "")))[:300], cell_style) for c in table.columns])
    t = Table(data, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaf0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b0b4c0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(t)
    if table.truncated:
        story += [Spacer(1, 8), Paragraph("Result truncated at the configured row limit.", styles["Italic"])]
    doc.build(story)
    return out.getvalue()


class ExportService:
    def __init__(self, cfg: WebMindConfig, db: Database, store: ObjectStore) -> None:
        self.cfg = cfg
        self.db = db
        self.store = store

    def create(
        self, principal: Principal, table: TableResult, fmt: ExportFormat, *, title: str, summary: str | None = None
    ) -> ExportRef:
        self.purge_expired()
        data = render_pdf(table, title, summary) if fmt == "pdf" else render(table, fmt, title)
        export_id = new_id()
        key = f"exports/{principal.tenant_id}/{export_id}.{fmt}"
        self.store.put(key, data)
        expires = datetime.now(timezone.utc) + timedelta(minutes=self.cfg.retention.export_ttl_minutes)
        filename = f"{_slug(title)}.{fmt}"
        with self.db.session() as s:
            s.add(
                ExportJob(
                    id=export_id,
                    tenant_id=principal.tenant_id,
                    user_key=principal.user_key,
                    dataset_id=table.dataset,
                    format=fmt,
                    filename=filename,
                    object_key=key,
                    row_count=len(table.rows),
                    size_bytes=len(data),
                    expires_at=expires,
                )
            )
        return ExportRef(
            id=export_id,
            format=fmt,
            filename=filename,
            row_count=len(table.rows),
            expires_at=expires,
            download_url=f"/v1/exports/{export_id}/download",
        )

    def open(self, principal: Principal, export_id: str) -> tuple[str, str, bytes]:
        with self.db.session() as s:
            job = s.get(ExportJob, export_id)
            # Same response for "missing" and "not yours" — no existence oracle.
            if (
                job is None
                or job.tenant_id != principal.tenant_id
                or job.user_key != principal.user_key
                or not principal.authenticated
            ):
                raise NotFoundError("export not found")
            expires = job.expires_at if job.expires_at.tzinfo else job.expires_at.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                raise NotFoundError("export expired")
            job.downloaded = True
            key, filename, fmt = job.object_key, job.filename, job.format
        return filename, MEDIA_TYPES[fmt], self.store.get(key)

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        removed = 0
        with self.db.session() as s:
            for job in s.execute(select(ExportJob)).scalars():
                exp = job.expires_at if job.expires_at.tzinfo else job.expires_at.replace(tzinfo=timezone.utc)
                if exp < now:
                    self.store.delete(job.object_key)
                    s.delete(job)
                    removed += 1
        return removed
