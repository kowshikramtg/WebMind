"""Metadata database schema (SQLAlchemy 2.0, portable across SQLite and PostgreSQL).

Resource identity (``resources``), content versions (``resource_versions`` /
``documents``) and indexed representations (``chunks`` / ``embeddings``) are managed
independently, as required by the architecture specification.

Users, roles and permissions are *not* stored here: identities come from the host
application (verified tokens) and role → capability mappings come from versioned
configuration. See docs/architecture/data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("tenant_id", "connector_id", "uri", name="uq_resource_uri"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    connector_id: Mapped[str] = mapped_column(String(64), index=True)
    uri: Mapped[str] = mapped_column(String(2048))
    public_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(1024))
    kind: Mapped[str] = mapped_column(String(32), default="text")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    access: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    change_hint: Mapped[str | None] = mapped_column(String(256))
    sync_method: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Outgoing links (website sources) so unchanged pages (HTTP 304) still feed discovery.
    links: Mapped[list[Any]] = mapped_column(JSON, default=list)

    versions: Mapped[list[ResourceVersion]] = relationship(
        back_populates="resource", cascade="all, delete-orphan", order_by="ResourceVersion.version"
    )


class ResourceVersion(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (UniqueConstraint("resource_id", "version", name="uq_resource_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    original_hash: Mapped[str | None] = mapped_column(String(64))
    original_size: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    resource: Mapped[Resource] = relationship(back_populates="versions")
    document: Mapped[Document | None] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )


class Document(Base):
    """Normalized (extracted) representation of one resource version."""

    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("resource_versions.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    section_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    version: Mapped[ResourceVersion] = relationship(back_populates="document")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_tenant_resource", "tenant_id", "resource_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(1024))
    heading_path: Mapped[list[Any]] = mapped_column(JSON, default=list)
    anchor: Mapped[str | None] = mapped_column(String(1024))
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    char_count: Mapped[int] = mapped_column(Integer)


class Embedding(Base):
    """Vectors for the built-in 'database' vector store (float32 little-endian blobs)."""

    __tablename__ = "embeddings"
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmbeddingCache(Base):
    """Content-keyed embedding cache so unchanged chunks are never re-embedded."""

    __tablename__ = "embedding_cache"
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class IndexSetting(Base):
    __tablename__ = "index_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    connector_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_key: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_key: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(512))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExportJob(Base):
    __tablename__ = "export_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_key: Mapped[str] = mapped_column(String(320), index=True)
    dataset_id: Mapped[str | None] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(8))
    filename: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(512))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)


class QueryMetric(Base):
    """Lightweight local latency metrics (no external telemetry)."""

    __tablename__ = "query_metrics"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(32))
    total_ms: Mapped[float] = mapped_column(Float)
    retrieval_ms: Mapped[float | None] = mapped_column(Float)
    llm_ms: Mapped[float | None] = mapped_column(Float)
