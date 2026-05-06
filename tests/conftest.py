"""Shared pytest fixtures."""

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from xblp_common.migrations import create_tables


def _enable_foreign_keys(dbapi_conn: Any, _connection_record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """In-memory SQLite engine with FK enforcement and all tables created.

    StaticPool ensures every Session uses the same underlying connection, which
    is required for in-memory SQLite (each new connection gets an empty DB).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(eng, "connect", _enable_foreign_keys)
    create_tables(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """Provide a fresh Session for each test, rolled back on teardown."""
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
