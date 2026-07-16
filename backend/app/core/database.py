"""
SQLAlchemy 2.x database foundation.

Design notes (see docs/phase-1-foundation.md for the full rationale):

  - SQLite is the deliberate choice for this hackathon (docs/phase-0-dataset-
    audit.md SS11). Foreign-key enforcement is off by default in SQLite, so we
    turn it on explicitly per-connection.
  - init_db() is additive and non-destructive: create_all() adds missing
    tables, then _additive_column_sync() adds missing COLUMNS to tables that
    already exist. Neither ever drops, truncates, or rewrites anything. The
    column sync exists because create_all alone silently leaves a pre-existing
    database missing every column added by a later phase -- see its docstring.
  - ORM models (app/models) and Pydantic schemas (app/schemas) are kept
    strictly separate; nothing in this module or app/models imports Pydantic,
    and API routes never return an ORM instance directly.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model in the application."""


def _build_engine() -> Engine:
    connect_args = {}
    if settings.is_sqlite:
        # Required for SQLite + a threaded server (FastAPI/uvicorn workers).
        connect_args["check_same_thread"] = False
        sqlite_path = settings.sqlite_file_path
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(settings.database_url, connect_args=connect_args, future=True)


engine = _build_engine()


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Per-connection SQLite PRAGMAs.

    foreign_keys=ON: SQLite does not enforce FOREIGN KEY constraints unless
    told to, on every single connection. Without this, orphaned rows (e.g. a
    Transaction pointing at a deleted Client) would be silently allowed.

    journal_mode=WAL + synchronous=NORMAL: the SQLite default (rollback
    journal + synchronous=FULL) fsyncs on every commit, which made the
    Phase 2 bulk loaders (e.g. 50,000-row transaction ingestion) take
    multiple minutes -- one fsync-heavy disk round trip per commit adds up
    fast. WAL mode batches writes into a write-ahead log and checkpoints
    periodically instead of fsyncing every commit; synchronous=NORMAL is the
    documented-safe pairing with WAL (still durable against application
    crashes, only a very narrow OS-crash-at-exactly-the-wrong-instant window
    is weaker than FULL). This is the right tradeoff for a hackathon-scale
    single-writer SQLite database. See docs/ARCHITECTURE_DECISIONS.md."""
    if not settings.is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _additive_column_sync() -> list[str]:
    """Add columns that exist on a model but not yet in the database.

    WHY THIS EXISTS
    ---------------
    `create_all()` creates missing TABLES but never ALTERs an existing one. Every
    phase that added a column to a table an earlier phase had already created --
    Phase 4 on `risk_events`, Phase 5 on `investigations`, Phase 6 on
    `human_reviews` and `sar_drafts` -- therefore left any pre-existing database
    silently missing those columns. The symptom is a 500 at runtime
    (`no such column: human_reviews.case_id`), and NO TEST CAN CATCH IT: the
    suite builds a fresh database every run, where create_all produces every
    column. It was found only by running the Phase 7 UI against a real dev DB.

    WHAT THIS IS, AND IS NOT
    ------------------------
    This is a deliberately minimal, ADDITIVE-ONLY reconciler. It only ever runs
    `ALTER TABLE ... ADD COLUMN` for a column the models declare and the database
    lacks. It NEVER drops, renames, retypes, backfills, or reorders anything, and
    it never touches a table create_all did not create.

    It is NOT a migration tool and must not grow into one. It cannot express a
    rename, a data backfill, or a destructive change -- and it deliberately
    refuses to try, because a half-built migration system that silently
    mis-migrates a compliance database is far worse than an honest error. The
    real answer is Alembic; this closes the specific, real gap that repeatedly
    bit this project, and nothing more.

    SAFETY
    ------
    * Additive only -- existing data is never read, moved, or deleted.
    * A new column must be NULLable or have a server default; SQLite cannot add a
      NOT NULL column without one. Such a column is SKIPPED and reported rather
      than guessed at, because inventing a default for a compliance field is
      exactly the kind of fabrication this project refuses.
    * Table/column names come from SQLAlchemy metadata (our own code), never from
      user input, so the interpolation below cannot carry injected SQL.
    """
    from sqlalchemy import inspect, text

    added: list[str] = []

    with engine.begin() as conn:
        # Inspect from the live CONNECTION, not the engine. An engine-level
        # Inspector caches reflected schema, so it can report a column set that
        # no longer matches the database -- which produces the exact failure
        # this function exists to prevent ("duplicate column name" on a column
        # the cache said was missing). Reflecting inside the transaction reads
        # the real current schema.
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())

        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all handles brand-new tables
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    logger.warning(
                        "Cannot auto-add NOT NULL column %s.%s without a server default. "
                        "Recreate the database or add the column manually.",
                        table.name,
                        column.name,
                    )
                    continue
                ddl = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))
                added.append(f"{table.name}.{column.name}")

    if added:
        logger.info("Added %d missing column(s): %s", len(added), ", ".join(added))
    return added


def init_db() -> None:
    """Create missing tables, then add missing columns. Safe to call on every
    startup: additive only, never drops or modifies existing data.

    Importing app.models here (rather than at module load time) ensures every
    model class is registered on Base.metadata before create_all runs.
    """
    import app.models  # noqa: F401  (registers all models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _additive_column_sync()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped session, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context-manager form of the same session lifecycle, for use outside of
    FastAPI request handling (scripts, tests, ingestion jobs)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
