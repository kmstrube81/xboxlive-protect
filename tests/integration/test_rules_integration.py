"""Integration tests for rules endpoints — require Linux, root, and nft.

Run with:
    sudo pytest -m integration

Verifies the full stack:
  1. POST /rules → IP appears in ``nft list set inet xblp blocklist`` within 1 s.
  2. DELETE /rules/{id} → IP removed from the same set.
  3. Audit log contains rule_added / rule_removed entries with non-null undo_tokens.

Microsoft/XBL IP rejection test is SKIPPED: the xbl_allowlist check is inert
until Phase 3 generates data/xbox-live-allowlist.json.  See CHANGELOG Stage 2.

Uses a temporary SQLite file (not :memory:) and the production NftManager
targeting the default ``xblp`` table, which is created at daemon startup.  The
table is removed at teardown.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as sa_create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from xblp_api.app import _DEFAULT_ADMIN_PASSWORD, _DEFAULT_ADMIN_USERNAME, create_app
from xblp_api.config import Settings
from xblp_common.migrations import create_tables
from xblp_common.models import AuditLog, EventType, Rule
from xblp_common.nft import NftError, NftManager

pytestmark = [pytest.mark.integration, pytest.mark.linux]

_TEST_TABLE = "xblp_rules_int_test"


def _skip_reason() -> str | None:
    if platform.system() != "Linux":
        return "rules integration tests require Linux"
    if not shutil.which("nft") and not Path("/usr/sbin/nft").exists():
        return "nft binary not found"
    import os as _os

    if _os.geteuid() != 0:
        return "rules integration tests require root (sudo pytest -m integration)"
    return None


_reason = _skip_reason()
if _reason:
    pytest.skip(_reason, allow_module_level=True)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_engine():
    eng = sa_create_engine(
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


def _make_settings() -> Settings:
    return Settings(
        db_path=":memory:",
        cookie_secure=False,
        nft_enabled=True,
        tls_enabled=False,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def nft_table():
    """Create the test nftables table; remove it at teardown."""
    mgr = NftManager(table=_TEST_TABLE)
    mgr.apply_initial_ruleset()
    try:
        yield mgr
    finally:
        with contextlib.suppress(NftError):
            mgr.remove_ruleset()


@pytest.fixture
async def api_client(nft_table):
    """ASGI test client wired to the rules integration test nft table."""
    from unittest.mock import patch

    engine = _make_engine()
    settings = _make_settings()
    app = create_app(settings=settings, engine=engine)

    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")
    await ac.__aenter__()
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()

    # Override app.state.nft_manager to use the test table name
    app.state.nft_manager = nft_table

    db = Session(engine)
    try:
        yield ac, db, nft_table
    finally:
        db.close()
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)


async def _login_and_change_pw(ac: AsyncClient) -> None:
    r = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert r.status_code == 200
    r2 = await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "inttest1!"},
    )
    assert r2.status_code == 204


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_post_rule_appears_in_nft_blocklist(api_client):
    """POST /rules → IP in nft blocklist within 1 second."""
    ac, db, mgr = api_client
    await _login_and_change_pw(ac)

    r = await ac.post(
        "/api/v1/rules",
        json={"ip_address": "203.0.113.45", "comment": "integration test"},
    )
    assert r.status_code == 201

    deadline = time.monotonic() + 1.0
    found = False
    while time.monotonic() < deadline:
        blocklist = mgr.list_blocklist()
        if any(ip == "203.0.113.45" for ip, _ in blocklist):
            found = True
            break
        time.sleep(0.05)

    assert found, f"203.0.113.45 not in blocklist after 1s; blocklist={mgr.list_blocklist()}"


async def test_delete_rule_removed_from_nft_blocklist(api_client):
    """DELETE /rules/{id} → IP removed from nft blocklist."""
    ac, db, mgr = api_client
    await _login_and_change_pw(ac)

    r_post = await ac.post(
        "/api/v1/rules",
        json={"ip_address": "203.0.113.46", "comment": "to delete"},
    )
    assert r_post.status_code == 201
    rule_id = r_post.json()["id"]

    # Confirm it appeared
    assert any(ip == "203.0.113.46" for ip, _ in mgr.list_blocklist())

    r_del = await ac.delete(f"/api/v1/rules/{rule_id}")
    assert r_del.status_code == 204

    deadline = time.monotonic() + 1.0
    removed = False
    while time.monotonic() < deadline:
        blocklist = mgr.list_blocklist()
        if not any(ip == "203.0.113.46" for ip, _ in blocklist):
            removed = True
            break
        time.sleep(0.05)

    assert removed, f"203.0.113.46 still in blocklist after delete; blocklist={mgr.list_blocklist()}"


async def test_audit_log_entries_after_add_and_delete(api_client):
    """POST then DELETE leaves correct audit_log entries with non-null undo_tokens."""
    ac, db, mgr = api_client
    await _login_and_change_pw(ac)

    r_post = await ac.post(
        "/api/v1/rules",
        json={"ip_address": "203.0.113.47"},
    )
    assert r_post.status_code == 201
    rule_id = r_post.json()["id"]

    r_del = await ac.delete(f"/api/v1/rules/{rule_id}")
    assert r_del.status_code == 204

    db.expire_all()
    added = db.query(AuditLog).filter_by(event_type=EventType.rule_added).first()
    assert added is not None
    assert added.undo_token is not None

    removed = db.query(AuditLog).filter_by(event_type=EventType.rule_removed).first()
    assert removed is not None
    assert removed.undo_token is not None


@pytest.mark.skip(
    reason=(
        "XBL allowlist check is inert in Stage 2 — data/xbox-live-allowlist.json is "
        "not generated until Phase 3.  Re-enable this test in Phase 3 once the "
        "allowlist file exists and load_xbl_allowlist() returns real Microsoft ranges."
    )
)
async def test_microsoft_ip_rejected_as_xbox_live(api_client):
    """POST with a Microsoft/XBL IP returns 422 with reason='xbox-live'."""
    ac, _db, _mgr = api_client
    await _login_and_change_pw(ac)
    # 13.107.0.0/16 is a known Microsoft range; pick a concrete IP inside it.
    r = await ac.post("/api/v1/rules", json={"ip_address": "13.107.0.1"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "xbox-live"
