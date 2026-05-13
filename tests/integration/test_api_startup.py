"""Integration tests for API startup on the R4S target (run with: pytest -m integration).

Tests in this module verify the end-to-end startup sequence on a real Linux host
with nft available:
  1. Startup on a clean DB installs the nftables `table inet xblp` if absent.
  2. Startup on a clean DB is a no-op when the table already exists.
  3. Default admin is seeded on a fresh DB and not duplicated on restart.

These tests are ONLY valid on Linux with root (nft requires root). They are
deselected on all other platforms by the `linux` marker.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from xblp_api.app import _DEFAULT_ADMIN_USERNAME, create_app
from xblp_api.config import Settings
from xblp_common.migrations import create_tables
from xblp_common.models import User

_TEST_TABLE = "xblp_test_api"  # avoid clobbering a production xblp table


def _fresh_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        eng,
        "connect",
        lambda conn, _: conn.cursor().execute("PRAGMA foreign_keys=ON"),
    )
    create_tables(eng)
    return eng


def _nft_table_present(table: str) -> bool:
    result = subprocess.run(
        ["/usr/sbin/nft", "list", "chain", "inet", table, "forward"],
        capture_output=True,
    )
    return result.returncode == 0


def _remove_nft_table(table: str) -> None:
    subprocess.run(
        ["/usr/sbin/nft", "delete", "table", "inet", table],
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def cleanup_nft_table():
    _remove_nft_table(_TEST_TABLE)
    yield
    _remove_nft_table(_TEST_TABLE)


def _make_settings(nft_table: str = _TEST_TABLE) -> Settings:
    return Settings(
        db_path=":memory:",
        cookie_secure=False,
        nft_enabled=True,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )


@pytest.mark.integration
@pytest.mark.linux
async def test_startup_installs_nft_table_when_absent(monkeypatch):
    """App startup creates table inet xblp_test on a clean host."""
    from xblp_common.nft import NftManager

    settings = _make_settings()
    engine = _fresh_engine()

    # Patch NftManager to use xblp_test table so we don't touch production
    monkeypatch.setattr(
        "xblp_api.app._apply_nft_ruleset",
        lambda s: NftManager(table=_TEST_TABLE).apply_initial_ruleset(),
    )

    assert not _nft_table_present(_TEST_TABLE)

    app = create_app(settings=settings, engine=engine)
    async with app.router.lifespan_context(app):
        assert _nft_table_present(_TEST_TABLE)


@pytest.mark.integration
@pytest.mark.linux
async def test_startup_nft_is_noop_when_table_already_present(monkeypatch):
    """Startup when table already exists is idempotent — no error, no duplicate."""
    from xblp_common.nft import NftManager

    settings = _make_settings()
    engine = _fresh_engine()

    mgr = NftManager(table=_TEST_TABLE)
    mgr.apply_initial_ruleset()
    assert _nft_table_present(_TEST_TABLE)

    monkeypatch.setattr(
        "xblp_api.app._apply_nft_ruleset",
        lambda s: NftManager(table=_TEST_TABLE).apply_initial_ruleset(),
    )

    app = create_app(settings=settings, engine=engine)
    async with app.router.lifespan_context(app):
        assert _nft_table_present(_TEST_TABLE)


@pytest.mark.integration
@pytest.mark.linux
async def test_default_admin_seeded_on_fresh_db(monkeypatch):
    """Fresh DB seeds admin; restart (second lifespan) doesn't duplicate."""
    monkeypatch.setattr("xblp_api.app._apply_nft_ruleset", lambda s: None)

    settings = _make_settings()
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = create_app(settings=settings, engine=engine)
    async with app.router.lifespan_context(app):
        with factory() as db:
            count = db.query(User).filter_by(username=_DEFAULT_ADMIN_USERNAME).count()
        assert count == 1

    # Simulate restart — same engine, same DB
    app2 = create_app(settings=settings, engine=engine)
    async with app2.router.lifespan_context(app2):
        with factory() as db:
            count = db.query(User).filter_by(username=_DEFAULT_ADMIN_USERNAME).count()
        assert count == 1
