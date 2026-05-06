"""Engine factory and session utilities (see DESIGN.md §4.1)."""

import os
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_DB_PATH = "/var/lib/xboxlive-protect/state.db"


def get_db_path() -> str:
    """Return the database file path from the environment, falling back to the production default."""
    return os.environ.get("XBLP_DB_PATH", _DEFAULT_DB_PATH)


def _configure_sqlite(dbapi_conn: Any, _connection_record: Any) -> None:
    """Apply SQLite connection pragmas required for correct operation."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(db_path: str | None = None, **kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine for the given SQLite database path.

    Enables WAL journal mode (better concurrent read performance) and foreign
    key enforcement (required for ON DELETE CASCADE to work in SQLite).
    """
    if db_path is None:
        db_path = get_db_path()
    url = f"sqlite:///{db_path}"
    engine = _sa_create_engine(url, **kwargs)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to *engine*."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
