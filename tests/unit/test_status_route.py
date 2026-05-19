"""Unit tests for GET /api/v1/status.

Uses httpx.AsyncClient with ASGITransport — no network, no nft, in-memory DB.

Coverage:
- GET /status: 401 without session, 403 when must_change_password=True
- capture_status='missing' when no snapshots
- capture_status='active' when latest snapshot is within 3 seconds
- capture_status='stale' when latest snapshot is older than 3 seconds
- active_profile: None when no runtime_state row, value when present
- rules_count: correct totals for local and subscription rules
- Response shape validation
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as sa_create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from xblp_api.app import (
    _DEFAULT_ADMIN_PASSWORD,
    _DEFAULT_ADMIN_USERNAME,
    create_app,
)
from xblp_api.config import Settings
from xblp_common.migrations import create_tables
from xblp_common.models import PeerSnapshot, Rule, RuntimeState

pytestmark = pytest.mark.unit


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
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


async def _build_client(settings: Settings):
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


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _login(ac: AsyncClient) -> AsyncClient:
    r = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert r.status_code == 200
    r2 = await ac.post(
        "/api/v1/auth/password",
        json={"old_password": _DEFAULT_ADMIN_PASSWORD, "new_password": "newpass123"},
    )
    assert r2.status_code == 204
    return ac


def _make_snapshot(captured_at: datetime) -> PeerSnapshot:
    return PeerSnapshot(
        captured_at=captured_at,
        peer_ip="203.0.113.1",
        pps=10.0,
        pps_5s=9.0,
        score=30.0,
        flagged=False,
        bytes_in=1000,
        bytes_out=500,
        first_seen_at=captured_at,
        last_seen_at=captured_at,
    )


def _make_rule(ip: str, source: str = "local") -> Rule:
    now = _now()
    return Rule(
        ip_address=ip,
        cidr_prefix=32,
        source=source,
        created_at=now,
        updated_at=now,
    )


# ── Auth gates ────────────────────────────────────────────────────────────────


async def test_get_status_requires_auth(client):
    ac, _ = client
    r = await ac.get("/api/v1/status")
    assert r.status_code == 401


async def test_get_status_blocked_by_must_change_password(client):
    ac, _ = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r = await ac.get("/api/v1/status")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "password_change_required"


# ── capture_status ────────────────────────────────────────────────────────────


async def test_status_capture_missing_when_no_snapshots(client):
    ac, _ = client
    await _login(ac)
    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["capture_status"] == "missing"
    assert body["capture_last_seen"] is None


async def test_status_capture_active_with_recent_snapshot(client):
    ac, db = client
    await _login(ac)

    # Snapshot within the last 3 seconds
    db.add(_make_snapshot(_now()))
    db.commit()

    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["capture_status"] == "active"
    assert body["capture_last_seen"] is not None


async def test_status_capture_stale_with_old_snapshot(client):
    ac, db = client
    await _login(ac)

    # Snapshot older than 3 seconds
    old_ts = _now() - timedelta(seconds=10)
    db.add(_make_snapshot(old_ts))
    db.commit()

    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["capture_status"] == "stale"


# ── active_profile ────────────────────────────────────────────────────────────


async def test_status_active_profile_none_when_no_runtime_state(client):
    ac, _ = client
    await _login(ac)
    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    assert r.json()["active_profile"] is None


async def test_status_active_profile_from_runtime_state(client):
    ac, db = client
    await _login(ac)

    db.add(RuntimeState(key="active_profile", value="mw2-x360", updated_at=_now()))
    db.commit()

    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    assert r.json()["active_profile"] == "mw2-x360"


# ── rules_count ───────────────────────────────────────────────────────────────


async def test_status_rules_count_empty(client):
    ac, _ = client
    await _login(ac)
    r = await ac.get("/api/v1/status")
    body = r.json()
    assert body["rules_count"]["total"] == 0
    assert body["rules_count"]["local"] == 0
    assert body["rules_count"]["subscription"] == 0


async def test_status_rules_count_mixed(client):
    ac, db = client
    await _login(ac)

    db.add(_make_rule("203.0.113.1", source="local"))
    db.add(_make_rule("203.0.113.2", source="local"))
    db.add(_make_rule("203.0.113.3", source="subscription:1"))
    db.commit()

    r = await ac.get("/api/v1/status")
    body = r.json()
    assert body["rules_count"]["total"] == 3
    assert body["rules_count"]["local"] == 2
    assert body["rules_count"]["subscription"] == 1


# ── Response shape ────────────────────────────────────────────────────────────


async def test_status_response_shape(client):
    ac, _ = client
    await _login(ac)
    r = await ac.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], int)
    assert body["uptime_seconds"] >= 0
    assert "active_profile" in body
    assert "capture_status" in body
    assert body["capture_status"] in ("active", "stale", "missing")
    assert "capture_last_seen" in body
    assert "rules_count" in body
    assert {"total", "local", "subscription"} == set(body["rules_count"].keys())
    assert "blocklist_size" in body
    assert isinstance(body["blocklist_size"], int)
