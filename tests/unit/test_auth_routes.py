"""Unit tests for auth routes: login, logout, me, password change.

Uses httpx.AsyncClient with ASGITransport — no network, no nft, in-memory DB.
Each test gets a fresh in-memory app via the `client` fixture.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as sa_create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from xblp_api.app import _DEFAULT_ADMIN_PASSWORD, _DEFAULT_ADMIN_USERNAME, create_app
from xblp_api.config import Settings
from xblp_common.migrations import create_tables
from xblp_common.models import AuditLog, EventType, User, UserSession

_COOKIE = "xblp_session"


def _make_settings(**overrides) -> Settings:
    base = dict(
        db_path=":memory:",
        cookie_secure=False,
        nft_enabled=False,
        tls_enabled=False,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )
    base.update(overrides)
    return Settings(**base)


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


async def _build_client(settings: Settings) -> tuple[AsyncClient, Session, object]:
    """Return (AsyncClient, db Session, app) for the given settings."""
    engine = _make_engine()
    app = create_app(settings=settings, engine=engine)
    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")
    await ac.__aenter__()
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    db = Session(engine)
    return ac, db, ctx


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[tuple[AsyncClient, Session], None]:
    settings = _make_settings()
    ac, db, ctx = await _build_client(settings)
    try:
        yield ac, db
    finally:
        db.close()
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)


# ── Admin seeding ─────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_default_admin_seeded(client):
    ac, db = client
    user = db.query(User).filter_by(username=_DEFAULT_ADMIN_USERNAME).first()
    assert user is not None
    assert user.must_change_password is True


@pytest.mark.unit
async def test_seed_is_idempotent(client):
    """Running seed logic twice doesn't create a second admin row."""
    from xblp_api.app import _seed_admin

    ac, db = client
    engine = db.get_bind()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings = _make_settings()
    _seed_admin(factory, settings)
    count = db.query(User).filter_by(username=_DEFAULT_ADMIN_USERNAME).count()
    assert count == 1


# ── Login ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_login_happy_path(client):
    ac, db = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == _DEFAULT_ADMIN_USERNAME
    assert resp.json()["must_change_password"] is True
    assert _COOKIE in resp.cookies


@pytest.mark.unit
async def test_login_wrong_password(client):
    ac, db = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.unit
async def test_login_unknown_user(client):
    ac, db = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "whatever"},
    )
    assert resp.status_code == 401


@pytest.mark.unit
async def test_login_failed_writes_audit_log(client):
    ac, db = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "bad"},
    )
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.login_failed).first()
    assert entry is not None
    assert entry.target == "nobody"


@pytest.mark.unit
async def test_login_success_writes_audit_log(client):
    ac, db = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.login).first()
    assert entry is not None
    assert entry.target == _DEFAULT_ADMIN_USERNAME


# ── /auth/me ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_me_unauthenticated_returns_401(client):
    ac, _ = client
    resp = await ac.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.unit
async def test_me_returns_must_change_password_true_for_seeded_admin(client):
    ac, _ = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)
    resp = await ac.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


# ── Forced password-change gate ───────────────────────────────────────────────


@pytest.mark.unit
def test_password_change_gate_error_shape():
    """require_password_changed raises 403 with the stable error shape."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from xblp_api.auth.dependencies import require_password_changed

    user = MagicMock()
    user.must_change_password = True
    with pytest.raises(HTTPException) as exc_info:
        require_password_changed(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "password_change_required"


@pytest.mark.unit
async def test_me_exempt_from_password_change_gate(client):
    """/auth/me uses current_user (not require_password_changed) so it works
    even when must_change_password=True."""
    ac, _ = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)
    resp = await ac.get("/api/v1/auth/me")
    assert resp.status_code == 200  # would be 403 if gate were applied


# ── /auth/password ────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_change_password_clears_flag(client):
    ac, db = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)

    resp = await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "newpass123"},
    )
    assert resp.status_code == 204

    db.expire_all()
    user = db.query(User).filter_by(username=_DEFAULT_ADMIN_USERNAME).first()
    assert user.must_change_password is False
    assert user.password_changed_at is not None


@pytest.mark.unit
async def test_change_password_writes_audit_log(client):
    ac, db = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)
    await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "newpass123"},
    )
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.password_changed).first()
    assert entry is not None


@pytest.mark.unit
async def test_change_password_wrong_old_password(client):
    ac, _ = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)
    resp = await ac.post(
        "/api/v1/auth/password",
        json={"old_password": "wrong", "new_password": "newpass123"},
    )
    assert resp.status_code == 400


@pytest.mark.unit
async def test_change_password_revokes_other_sessions(client):
    ac, db = client
    # Log in twice to create two sessions
    r1 = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r2 = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    session2_id = r2.cookies.get(_COOKIE)

    # Use session 1 to change password
    ac.cookies.update(r1.cookies)
    await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "newpass123"},
    )

    db.expire_all()
    remaining = db.query(UserSession).filter_by(id=session2_id).first()
    assert remaining is None


@pytest.mark.unit
async def test_change_password_keeps_current_session(client):
    ac, _ = client
    login = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    ac.cookies.update(login.cookies)
    await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "newpass123"},
    )
    me = await ac.get("/api/v1/auth/me")
    assert me.status_code == 200


# ── Cookie attributes ─────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_cookie_is_httponly(client):
    ac, _ = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert "httponly" in resp.headers.get("set-cookie", "").lower()


@pytest.mark.unit
async def test_cookie_samesite_strict(client):
    ac, _ = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert "samesite=strict" in resp.headers.get("set-cookie", "").lower()


@pytest.mark.unit
async def test_cookie_not_secure_when_config_says_so(client):
    """cookie_secure=False in _TEST_SETTINGS — Secure flag must be absent."""
    ac, _ = client
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert "secure" not in resp.headers.get("set-cookie", "").lower()


@pytest.mark.unit
async def test_cookie_secure_when_config_says_so():
    """A separate app with cookie_secure=True should include the Secure flag."""
    settings = _make_settings(cookie_secure=True)
    ac, db, ctx = await _build_client(settings)
    try:
        resp = await ac.post(
            "/api/v1/auth/login",
            json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
        )
        assert "secure" in resp.headers.get("set-cookie", "").lower()
    finally:
        db.close()
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)
