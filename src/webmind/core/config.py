"""Typed, validated WebMind configuration.

Configuration is loaded from a YAML file. Values of the form ``${ENV_VAR}`` or
``${ENV_VAR:-default}`` are substituted from the environment so that secrets never
need to be written into the YAML file itself.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from webmind.core.errors import ConfigurationError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------- app


class AppConfig(_Model):
    name: str = "webmind"
    environment: Literal["development", "staging", "production", "test"] = "development"
    default_tenant: str = Field(default="default", pattern=_ID_PATTERN)
    data_dir: str = "./data"


class ServerConfig(_Model):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)
    admin_api_key: SecretStr | None = None
    rate_limit_per_minute: int = Field(default=60, ge=0, description="0 disables rate limiting")
    max_query_chars: int = Field(default=4000, ge=16, le=100_000)
    serve_widget: bool = True


class DatabaseConfig(_Model):
    url: str = "sqlite:///./data/webmind.db"
    echo: bool = False


class VectorStoreConfig(_Model):
    provider: Literal["database", "pgvector"] = "database"


class ObjectStoreConfig(_Model):
    provider: Literal["local"] = "local"
    path: str = "./data/objects"


# --------------------------------------------------------------------- providers


class LLMConfig(_Model):
    provider: Literal["none", "ollama", "openai", "anthropic"] = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=16, le=128_000)
    timeout_seconds: float = Field(default=120.0, gt=0)
    use_for_planning: bool = True
    # Anthropic-specific options (ignored by other providers).
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None, description="Anthropic output_config.effort; None uses the model default"
    )
    refusal_fallback: Literal["default", "none"] = Field(
        default="default", description="Anthropic server-side refusal fallback routing"
    )

    @model_validator(mode="after")
    def _check(self) -> LLMConfig:
        if self.provider != "none" and not self.model:
            raise ValueError(f"llm.model is required when llm.provider is '{self.provider}'")
        if self.provider == "anthropic" and self.api_key is None:
            raise ValueError("llm.api_key is required for the anthropic provider")
        return self


class EmbeddingsConfig(_Model):
    provider: Literal["hashing", "ollama", "openai", "sentence_transformers"] = "hashing"
    model: str | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    dimensions: int | None = Field(default=None, ge=8, le=8192)
    batch_size: int = Field(default=32, ge=1, le=2048)
    timeout_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> EmbeddingsConfig:
        if self.provider != "hashing" and not self.model:
            raise ValueError(f"embeddings.model is required when embeddings.provider is '{self.provider}'")
        return self


class RerankingConfig(_Model):
    provider: Literal["none", "cross_encoder"] = "none"
    model: str | None = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class RetrievalConfig(_Model):
    hybrid_search: bool = True
    top_k: int = Field(default=6, ge=1, le=50)
    candidate_k: int = Field(default=30, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1)
    min_semantic_score: float = Field(default=0.15, ge=-1.0, le=1.0)
    reranking: RerankingConfig = Field(default_factory=RerankingConfig)


class IngestionConfig(_Model):
    chunk_size: int = Field(default=1200, ge=200, le=20_000)
    chunk_overlap: int = Field(default=150, ge=0, le=5_000)
    store_originals: bool = True
    max_document_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)

    @model_validator(mode="after")
    def _check(self) -> IngestionConfig:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("ingestion.chunk_overlap must be smaller than ingestion.chunk_size")
        return self


class RetentionConfig(_Model):
    keep_versions: int = Field(default=5, ge=1, le=1000)
    export_ttl_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    audit_days: int = Field(default=365, ge=1)


class SynchronizationConfig(_Model):
    enabled: bool = True
    mode: Literal["incremental"] = "incremental"
    scheduler: bool = Field(default=False, description="Run periodic syncs inside the API process")


# ---------------------------------------------------------------------- security


class AccessRule(_Model):
    """Declarative access rule attached to a source, path rule, or dataset.

    ``visibility: public`` grants access to everyone in the tenant (including anonymous
    visitors). ``restricted`` requires the principal to hold at least one of ``roles``
    (if given) AND match every attribute in ``attributes`` (if given).
    """

    visibility: Literal["public", "restricted"] = "restricted"
    roles: list[str] = Field(default_factory=list)
    attributes: dict[str, str | list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> AccessRule:
        if self.visibility == "public" and (self.roles or self.attributes):
            raise ValueError("a public access rule cannot also declare roles or attributes")
        return self


class RoleConfig(_Model):
    capabilities: list[str] = Field(default_factory=list)
    inherits: list[str] = Field(default_factory=list)


class JWTConfig(_Model):
    algorithms: list[Literal["HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256"]] = Field(
        default_factory=lambda: ["HS256"]
    )
    secret: SecretStr | None = None
    public_key: str | None = Field(default=None, description="PEM public key or path to one")
    issuer: str | None = None
    audience: str | None = None
    leeway_seconds: int = Field(default=30, ge=0, le=600)
    max_token_lifetime_seconds: int = Field(default=3600, ge=60)
    user_id_claim: str = "sub"
    roles_claim: str = "roles"
    tenant_claim: str = "tenant"
    attributes_claim: str = "attributes"


class AuthenticationConfig(_Model):
    mode: Literal["public", "host_application"] = "public"
    allow_anonymous: bool = True
    anonymous_roles: list[str] = Field(default_factory=lambda: ["public"])
    jwt: JWTConfig = Field(default_factory=JWTConfig)

    @model_validator(mode="after")
    def _check(self) -> AuthenticationConfig:
        if self.mode == "host_application":
            hs = any(a.startswith("HS") for a in self.jwt.algorithms)
            asym = any(not a.startswith("HS") for a in self.jwt.algorithms)
            if hs and asym:
                raise ValueError("do not mix symmetric (HS*) and asymmetric JWT algorithms")
            if hs and self.jwt.secret is None:
                raise ValueError("security.authentication.jwt.secret is required for HS* algorithms")
            if hs and self.jwt.secret is not None and len(self.jwt.secret.get_secret_value()) < 32:
                raise ValueError("security.authentication.jwt.secret must be at least 32 characters")
            if asym and not self.jwt.public_key:
                raise ValueError("security.authentication.jwt.public_key is required for RS*/ES* algorithms")
        return self


def _default_roles() -> dict[str, RoleConfig]:
    return {
        "public": RoleConfig(capabilities=["chat", "search", "navigate", "documents:read"]),
        "admin": RoleConfig(capabilities=["records:read", "analytics", "export", "reports"], inherits=["public"]),
    }


class SecurityConfig(_Model):
    authentication: AuthenticationConfig = Field(default_factory=AuthenticationConfig)
    authorization: Literal["enabled"] = "enabled"
    read_only_default: bool = True
    roles: dict[str, RoleConfig] = Field(default_factory=_default_roles)

    @field_validator("read_only_default")
    @classmethod
    def _read_only(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "read_only_default: false is not supported in WebMind 0.1 — source write "
                "operations are not implemented and read-only mode is a security boundary"
            )
        return v

    @model_validator(mode="after")
    def _check_roles(self) -> SecurityConfig:
        for name, role in self.roles.items():
            for parent in role.inherits:
                if parent not in self.roles:
                    raise ValueError(f"role '{name}' inherits unknown role '{parent}'")
        for r in self.authentication.anonymous_roles:
            if r not in self.roles:
                raise ValueError(f"anonymous role '{r}' is not defined in security.roles")
        return self


# ----------------------------------------------------------------------- sources


class PathAccessRule(_Model):
    pattern: str = Field(description="Regular expression matched against the resource URI/path")
    access: AccessRule

    @field_validator("pattern")
    @classmethod
    def _compile(cls, v: str) -> str:
        re.compile(v)
        return v


class SyncSchedule(_Model):
    interval_minutes: int | None = Field(default=None, ge=1)


class _SourceBase(_Model):
    id: str = Field(pattern=_ID_PATTERN)
    tenant: str | None = Field(default=None, pattern=_ID_PATTERN)
    access: AccessRule = Field(default_factory=lambda: AccessRule(visibility="public"))
    path_rules: list[PathAccessRule] = Field(default_factory=list)
    sync: SyncSchedule = Field(default_factory=SyncSchedule)
    webhook_secret: SecretStr | None = None


class WebsiteSourceConfig(_SourceBase):
    type: Literal["website"]
    url: str
    use_sitemap: bool = True
    crawl: bool = True
    max_pages: int = Field(default=500, ge=1, le=100_000)
    max_depth: int = Field(default=5, ge=0, le=50)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    respect_robots_txt: bool = True
    request_delay_seconds: float = Field(default=0.2, ge=0)
    timeout_seconds: float = Field(default=20.0, gt=0)
    user_agent: str = "WebMindBot/0.1 (+self-hosted; https://github.com/kowshikramtg/WebMind)"
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not re.match(r"^https?://", v):
            raise ValueError("website url must start with http:// or https://")
        return v


class FilesystemSourceConfig(_SourceBase):
    type: Literal["filesystem"]
    path: str
    patterns: list[str] = Field(default_factory=lambda: ["**/*.html", "**/*.md", "**/*.txt", "**/*.pdf"])
    exclude: list[str] = Field(default_factory=list)
    base_url: str | None = Field(
        default=None, description="Public URL that maps to `path`, used to build citation links"
    )


SourceConfig = Annotated[WebsiteSourceConfig | FilesystemSourceConfig, Field(discriminator="type")]


# ---------------------------------------------------------------------- datasets


class ColumnConfig(_Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    type: Literal["string", "integer", "number", "date", "datetime", "boolean"] = "string"
    description: str | None = None
    label: str | None = None
    filterable: bool = True
    groupable: bool = False
    aggregatable: bool = False
    values: list[str] | None = Field(
        default=None, description="Known categorical values; helps natural-language filter extraction"
    )


class RowFilterConfig(_Model):
    """Mandatory row-level filter applied to every query against a dataset.

    The column must equal the principal's attribute ``equals_attribute``. Principals that
    hold one of ``unless_roles`` are exempt. If the principal lacks the attribute, the
    filter matches nothing (deny-by-default).
    """

    column: str
    equals_attribute: str
    unless_roles: list[str] = Field(default_factory=list)


class DateWindowConfig(_Model):
    """Restrict queryable dates using principal attributes (ISO dates)."""

    from_attribute: str | None = None
    to_attribute: str | None = None
    unless_roles: list[str] = Field(default_factory=list)


class DatasetConfig(_Model):
    id: str = Field(pattern=_ID_PATTERN)
    tenant: str | None = Field(default=None, pattern=_ID_PATTERN)
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    connection: str = Field(description="SQLAlchemy URL; should use a read-only database role")
    table: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$")
    columns: list[ColumnConfig]
    date_column: str | None = None
    tenant_column: str | None = None
    row_filters: list[RowFilterConfig] = Field(default_factory=list)
    date_window: DateWindowConfig | None = None
    access: AccessRule = Field(default_factory=AccessRule)
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)

    @model_validator(mode="after")
    def _check(self) -> DatasetConfig:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"dataset '{self.id}' has duplicate column names")
        for ref in [self.date_column, self.tenant_column, *[f.column for f in self.row_filters]]:
            if ref is not None and ref not in names:
                raise ValueError(f"dataset '{self.id}' references unknown column '{ref}'")
        if self.date_column:
            col = next(c for c in self.columns if c.name == self.date_column)
            if col.type not in ("date", "datetime"):
                raise ValueError(f"dataset '{self.id}': date_column must be of type date or datetime")
        return self

    def column(self, name: str) -> ColumnConfig | None:
        return next((c for c in self.columns if c.name == name), None)


# ------------------------------------------------------------------------- root


class OrchestrationConfig(_Model):
    engine: Literal["native", "langgraph"] = "native"
    max_tool_calls: int = Field(default=4, ge=1, le=16)
    history_turns: int = Field(default=6, ge=0, le=50)


class TelemetryConfig(_Model):
    """Telemetry is off by default and never sent to a WebMind-operated service."""

    enabled: bool = False
    otlp_endpoint: str | None = None


class WebMindConfig(_Model):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    object_store: ObjectStoreConfig = Field(default_factory=ObjectStoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    synchronization: SynchronizationConfig = Field(default_factory=SynchronizationConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    datasets: list[DatasetConfig] = Field(default_factory=list)

    # Directory the config file was loaded from; relative paths resolve against it.
    base_dir: str = Field(default=".", exclude=True)

    @model_validator(mode="after")
    def _check(self) -> WebMindConfig:
        ids = [s.id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("source ids must be unique")
        dids = [d.id for d in self.datasets]
        if len(set(dids)) != len(dids):
            raise ValueError("dataset ids must be unique")
        if self.vector_store.provider == "pgvector" and not self.database.url.startswith("postgresql"):
            raise ValueError("vector_store.provider 'pgvector' requires a postgresql database.url")
        if self.app.environment == "production":
            if "*" in self.server.cors_origins:
                raise ValueError("server.cors_origins must not contain '*' in production")
        return self

    # -- helpers -------------------------------------------------------------
    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (Path(self.base_dir) / path).resolve()

    def source(self, source_id: str) -> SourceConfig:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)

    def dataset(self, dataset_id: str) -> DatasetConfig:
        for d in self.datasets:
            if d.id == dataset_id:
                return d
        raise KeyError(dataset_id)

    def tenant_of(self, item: _SourceBase | DatasetConfig) -> str:
        return item.tenant or self.app.default_tenant


# ----------------------------------------------------------------------- loading


def _substitute_env(value: Any, missing: list[str]) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            missing.append(name)
            return ""

        out = _ENV_PATTERN.sub(repl, value)
        return out
    if isinstance(value, dict):
        return {k: _substitute_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v, missing) for v in value]
    return value


def _drop_empty_secrets(data: Any) -> Any:
    """Treat empty strings produced by unset optional env vars as 'not configured'."""
    if isinstance(data, dict):
        return {k: _drop_empty_secrets(v) for k, v in data.items() if v != ""}
    if isinstance(data, list):
        return [_drop_empty_secrets(v) for v in data]
    return data


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def config_from_dict(data: dict[str, Any], base_dir: str | Path = ".") -> WebMindConfig:
    missing: list[str] = []
    data = _substitute_env(data or {}, missing)
    data = _drop_empty_secrets(data)
    try:
        cfg = WebMindConfig.model_validate({**data, "base_dir": str(Path(base_dir).resolve())})
    except Exception as exc:  # pydantic.ValidationError
        hint = f" (unset environment variables: {', '.join(sorted(set(missing)))})" if missing else ""
        raise ConfigurationError(f"invalid WebMind configuration{hint}:\n{exc}") from exc
    return cfg


def load_config(path: str | Path | None = None) -> WebMindConfig:
    """Load configuration from ``path`` (or $WEBMIND_CONFIG, or ./webmind.yaml)."""
    p = Path(path or os.environ.get("WEBMIND_CONFIG", "webmind.yaml"))
    if not p.is_file():
        raise ConfigurationError(f"configuration file not found: {p}")
    load_dotenv(p.parent / ".env")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"could not parse {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{p} must contain a YAML mapping")
    return config_from_dict(data, base_dir=p.parent)


def parse_iso_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
