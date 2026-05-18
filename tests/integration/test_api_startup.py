"""Integration tests for API startup on the R4S target (run with: pytest -m integration).

Tests in this module verify the end-to-end startup sequence on a real Linux host
with nft available:
  1. Startup on a clean DB installs the nftables `table inet xblp` if absent.
  2. Startup on a clean DB is a no-op when the table already exists.
  3. Default admin is seeded on a fresh DB and not duplicated on restart.
  4. xblp-api.service (via systemd) creates table inet xblp using CAP_NET_ADMIN.
  5. Daemon exits 1 and logs an error when state.db is root-owned.

Tests 4–5 are systemd end-to-end tests that require the service to be installed
via install-stage1.sh. They are skipped automatically when the service is absent.

These tests are ONLY valid on Linux with root (nft requires root). They are
deselected on all other platforms by the `linux` marker.
"""

import datetime
import os
import subprocess
import time
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
        tls_enabled=False,
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


# ── Systemd end-to-end tests (require install-stage1.sh to have run) ──────────


def _service_installed() -> bool:
    return subprocess.run(
        ["systemctl", "cat", "xblp-api.service"],
        capture_output=True,
    ).returncode == 0


def _service_active() -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "xblp-api"],
        capture_output=True,
        text=True,
    ).stdout.strip() == "active"


@pytest.mark.integration
@pytest.mark.linux
def test_service_creates_nft_table_via_systemctl():
    """xblp-api.service creates table inet xblp via CAP_NET_ADMIN when absent.

    This exercises the actual service user path (xblp) and confirms the unit
    file grants sufficient capabilities. The in-process tests above run as root
    and do not test this.
    """
    if not _service_installed():
        pytest.skip("xblp-api.service not installed; run install-stage1.sh first")
    if os.geteuid() != 0:
        pytest.skip("systemctl restart requires root")

    _remove_nft_table("xblp")
    assert not _nft_table_present("xblp")

    subprocess.run(["systemctl", "reset-failed", "xblp-api"], check=False)
    subprocess.run(["systemctl", "restart", "xblp-api"], check=True)

    deadline = time.monotonic() + 15
    table_found = False
    while time.monotonic() < deadline:
        if _service_active() and _nft_table_present("xblp"):
            table_found = True
            break
        time.sleep(0.5)

    assert table_found, (
        "xblp-api.service did not create table inet xblp within 15 s; "
        "check: journalctl -u xblp-api -n 30 --no-pager"
    )


@pytest.fixture
def root_owned_state_db():
    """Stop service, chown state.db to root, yield path, then restore and restart."""
    db = Path("/var/lib/xboxlive-protect/state.db")
    if not db.exists():
        pytest.skip("state.db not found; start xblp-api at least once first")
    if not _service_installed():
        pytest.skip("xblp-api.service not installed; run install-stage1.sh first")
    if os.geteuid() != 0:
        pytest.skip("chown requires root")

    subprocess.run(["systemctl", "stop", "xblp-api"], check=False)
    subprocess.run(["chown", "root:root", str(db)], check=True)
    try:
        yield db
    finally:
        subprocess.run(["chown", "xblp:xblp", str(db)], check=False)
        subprocess.run(["systemctl", "reset-failed", "xblp-api"], check=False)
        subprocess.run(["systemctl", "start", "xblp-api"], check=False)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _service_active():
                break
            time.sleep(0.5)


@pytest.mark.integration
@pytest.mark.linux
def test_root_owned_db_causes_startup_failure(root_owned_state_db):
    """Daemon exits 1 when state.db is root-owned; error appears in journal."""
    since = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subprocess.run(["systemctl", "reset-failed", "xblp-api"], check=False)
    subprocess.run(["systemctl", "start", "xblp-api"], check=False)
    time.sleep(5)

    assert not _service_active(), (
        "xblp-api should have exited 1 with root-owned state.db but is still active"
    )

    journal = subprocess.run(
        ["journalctl", "-u", "xblp-api", "--since", since, "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
    ).stdout
    assert "database file is not writable" in journal, (
        f"Expected 'database file is not writable' in journal since {since}; got:\n{journal}"
    )
