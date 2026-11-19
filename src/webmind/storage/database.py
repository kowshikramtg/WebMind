"""Metadata database runtime (engine, sessions, schema bootstrap)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from webmind.core.errors import ConfigurationError
from webmind.storage.models import Base, IndexSetting

_SQLITE_FTS_DDL = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        content, chunk_id UNINDEXED, tokenize = 'porter unicode61')""",
    """CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(content, chunk_id) VALUES (
            coalesce((SELECT title FROM resources WHERE id = new.resource_id), '') || ' ' ||
            coalesce(new.heading, '') || ' ' || new.text, new.id);
       END""",
    """CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
        DELETE FROM chunks_fts WHERE chunk_id = old.id;
       END""",
]

_POSTGRES_FTS_DDL = [
    """ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector
       GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading, '') || ' ' || text)) STORED""",
    "CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING GIN (tsv)",
]


def _resolve_sqlite_url(url: str, base_dir: str | Path) -> str:
    u = make_url(url)
    db = u.database
    if db and db != ":memory:" and not db.startswith("file:"):
        p = Path(db)
        if not p.is_absolute():
            p = (Path(base_dir) / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        u = u.set(database=str(p))
    return u.render_as_string(hide_password=False)


class Database:
    def __init__(self, url: str, *, base_dir: str | Path = ".", echo: bool = False) -> None:
        self.url = url
        backend = make_url(url).get_backend_name()
        if backend not in ("sqlite", "postgresql"):
            raise ConfigurationError(f"unsupported metadata database '{backend}' (supported: sqlite, postgresql)")
        self.dialect = backend
        kwargs: dict = {"echo": echo, "future": True}
        if backend == "sqlite":
            url = _resolve_sqlite_url(url, base_dir)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs["pool_pre_ping"] = True
        self.engine: Engine = create_engine(url, **kwargs)
        if backend == "sqlite":
            event.listen(self.engine, "connect", self._sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            ddl = _SQLITE_FTS_DDL if self.dialect == "sqlite" else _POSTGRES_FTS_DDL
            for stmt in ddl:
                conn.execute(text(stmt))

    def check_fts(self) -> bool:
        try:
            with self.engine.connect() as conn:
                if self.dialect == "sqlite":
                    conn.execute(text("SELECT count(*) FROM chunks_fts"))
                else:
                    conn.execute(text("SELECT to_tsvector('english', 'ok')"))
            return True
        except Exception:
            return False

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessions()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    # -- small key/value settings ---------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self.session() as s:
            row = s.get(IndexSetting, key)
            return row.value if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.session() as s:
            row = s.get(IndexSetting, key)
            if row:
                row.value = value
            else:
                s.add(IndexSetting(key=key, value=value))

    def bump_generation(self, tenant_id: str) -> None:
        key = f"index_generation:{tenant_id}"
        with self.session() as s:
            row = s.execute(select(IndexSetting).where(IndexSetting.key == key)).scalar_one_or_none()
            if row:
                row.value = str(int(row.value) + 1)
            else:
                s.add(IndexSetting(key=key, value="1"))

    def generation(self, tenant_id: str) -> int:
        v = self.get_setting(f"index_generation:{tenant_id}")
        return int(v) if v else 0

    def dispose(self) -> None:
        self.engine.dispose()
