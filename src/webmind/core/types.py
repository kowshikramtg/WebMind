"""Domain types shared across WebMind modules.

These are the stable contracts between layers (API ⇄ intelligence ⇄ governance ⇄
adapters). They intentionally contain no infrastructure-specific details.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------- identity


class Principal(BaseModel):
    """A verified caller. Produced only by the identity layer, never by the LLM."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str | None = None
    roles: frozenset[str] = frozenset()
    attributes: dict[str, Any] = Field(default_factory=dict)
    authenticated: bool = False
    capabilities: frozenset[str] = frozenset()

    @property
    def user_key(self) -> str:
        """Stable key used to scope per-user state (conversations, exports)."""
        return f"user:{self.user_id}" if self.authenticated and self.user_id else "anonymous"

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


# ------------------------------------------------------------------- resources


class ResourceStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"
    ERROR = "error"


class Freshness(BaseModel):
    mode: Literal["indexed", "live"] = "indexed"
    last_synced_at: datetime | None = None
    source_changed_at: datetime | None = None
    sync_method: str | None = None


class Section(BaseModel):
    """A logical section of a normalized document (e.g. text under one heading)."""

    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    anchor: str | None = None
    text: str
    page: int | None = None


class ParsedDocument(BaseModel):
    title: str | None = None
    description: str | None = None
    sections: list[Section] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    canonical_url: str | None = None
    noindex: bool = False
    content_type: str = "text/plain"

    @property
    def text(self) -> str:
        parts: list[str] = []
        for s in self.sections:
            if s.heading:
                parts.append(s.heading)
            parts.append(s.text)
        return "\n\n".join(p for p in parts if p)


# ------------------------------------------------------------------- retrieval


class RetrievedChunk(BaseModel):
    chunk_id: str
    resource_id: str
    text: str
    title: str | None = None
    heading: str | None = None
    url: str | None = None
    anchor_url: str | None = None
    score: float = 0.0
    semantic_score: float | None = None
    keyword_score: float | None = None
    version: int | None = None
    freshness: Freshness = Field(default_factory=Freshness)


class Citation(BaseModel):
    index: int
    resource_id: str
    title: str | None = None
    section: str | None = None
    url: str | None = None
    snippet: str
    version: int | None = None
    freshness: Freshness = Field(default_factory=Freshness)


class NavigationLink(BaseModel):
    title: str | None
    section: str | None
    url: str | None
    snippet: str
    resource_id: str


# --------------------------------------------------------------------- outputs


class ChartSeries(BaseModel):
    key: str
    label: str


class ChartSpec(BaseModel):
    """Renderer-agnostic chart specification (Recharts/Plotly/SVG can all consume it)."""

    type: Literal["bar", "line", "area", "pie", "scatter", "histogram", "table"]
    title: str
    x_key: str
    x_label: str | None = None
    y_label: str | None = None
    series: list[ChartSeries]
    data: list[dict[str, Any]]
    source: dict[str, Any] = Field(default_factory=dict)


class TableResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool = False
    dataset: str | None = None
    applied_filters: list[str] = Field(default_factory=list)
    # SQL actually executed. Audit-only: excluded from API responses to avoid leaking schema.
    executed_sql: str | None = Field(default=None, exclude=True)


class ExportRef(BaseModel):
    id: str
    format: str
    filename: str
    row_count: int
    expires_at: datetime
    download_url: str


class ToolTrace(BaseModel):
    tool: str
    arguments: dict[str, Any]
    status: Literal["ok", "denied", "error"]
    message: str | None = None
    duration_ms: float | None = None


class ChatResponse(BaseModel):
    answer: str
    intent: str
    conversation_id: str
    citations: list[Citation] = Field(default_factory=list)
    navigation: list[NavigationLink] = Field(default_factory=list)
    table: TableResult | None = None
    chart: ChartSpec | None = None
    export: ExportRef | None = None
    statistics: dict[str, Any] | None = None
    tools: list[ToolTrace] = Field(default_factory=list)
    grounded: bool = True
    llm_used: bool = False
    warnings: list[str] = Field(default_factory=list)
