"""Unit tests for the rules endpoints.

Uses httpx.AsyncClient with ASGITransport — no network, no nft, in-memory DB.
Each test gets a fresh app via the client fixture.

Coverage:
- Auth gate: every endpoint returns 401 without a session
- Forced-password gate: every endpoint returns 403 when must_change_password=True
- GET /rules: empty list, filters (source, since, search), pagination, totals
- POST /rules: happy path, each is_ip_blockable rejection, 409 conflict
- PATCH /rules/{id}: happy path, no-op (unchanged), subscription 403, 404
- DELETE /rules/{id}: happy path, subscription 403, 404
- POST /rules/{id}/promote: subscription→local (204), local no-op (204), 404
- Audit log: every successful mutation writes right event_type + non-null undo_token
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as sa_create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from xblp_api.app import _DEFAULT_ADMIN_PASSWORD, _DEFAULT_ADMIN_USERNAME, create_app
from xblp_api.config import Settings
from xblp_common.migrations import create_tables
from xblp_common.models import AuditLog, EventType, Rule, User

pytestmark = pytest.mark.unit

_COOKIE = "xblp_session"


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


async def _build_client(settings: Settings) -> tuple[AsyncClient, Session, object]:
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


def _make_rule(
    ip: str = "203.0.113.10",
    cidr: int = 32,
    source: str = "local",
    comment: str | None = None,
    subscription_id: int | None = None,
) -> Rule:
    now = _now()
    return Rule(
        ip_address=ip,
        cidr_prefix=cidr,
        source=source,
        subscription_id=subscription_id,
        comment=comment,
        created_at=now,
        updated_at=now,
    )


async def _login(ac: AsyncClient) -> AsyncClient:
    """Log in and change password; returns the client with session cookie set."""
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


# ── Auth gate (401 without session) ──────────────────────────────────────────


async def test_get_rules_requires_auth(client):
    ac, _ = client
    r = await ac.get("/api/v1/rules")
    assert r.status_code == 401


async def test_post_rule_requires_auth(client):
    ac, _ = client
    r = await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.1"})
    assert r.status_code == 401


async def test_patch_rule_requires_auth(client):
    ac, _ = client
    r = await ac.patch("/api/v1/rules/1", json={"comment": "x"})
    assert r.status_code == 401


async def test_delete_rule_requires_auth(client):
    ac, _ = client
    r = await ac.delete("/api/v1/rules/1")
    assert r.status_code == 401


async def test_promote_rule_requires_auth(client):
    ac, _ = client
    r = await ac.post("/api/v1/rules/1/promote")
    assert r.status_code == 401


# ── Forced-password gate (403 must_change_password=True) ─────────────────────


async def test_get_rules_blocked_by_must_change_password(client):
    ac, _ = client
    r = await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    assert r.status_code == 200
    r2 = await ac.get("/api/v1/rules")
    assert r2.status_code == 403
    assert r2.json()["detail"]["error"] == "password_change_required"


async def test_post_rule_blocked_by_must_change_password(client):
    ac, _ = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r = await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.1"})
    assert r.status_code == 403


async def test_patch_rule_blocked_by_must_change_password(client):
    ac, _ = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r = await ac.patch("/api/v1/rules/1", json={"comment": "x"})
    assert r.status_code == 403


async def test_delete_rule_blocked_by_must_change_password(client):
    ac, _ = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r = await ac.delete("/api/v1/rules/1")
    assert r.status_code == 403


async def test_promote_rule_blocked_by_must_change_password(client):
    ac, _ = client
    await ac.post(
        "/api/v1/auth/login",
        json={"username": _DEFAULT_ADMIN_USERNAME, "password": _DEFAULT_ADMIN_PASSWORD},
    )
    r = await ac.post("/api/v1/rules/1/promote")
    assert r.status_code == 403


# ── GET /rules ────────────────────────────────────────────────────────────────


async def test_get_rules_empty(client):
    ac, _ = client
    await _login(ac)
    r = await ac.get("/api/v1/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["limit"] == 100
    assert body["offset"] == 0


async def test_get_rules_returns_rule(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.5"))
    db.commit()
    r = await ac.get("/api/v1/rules")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["ip_address"] == "203.0.113.5"


async def test_get_rules_filter_source_local(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.5", source="local"))
    db.add(_make_rule("203.0.113.6", source="subscription:1"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"source": "local"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["ip_address"] == "203.0.113.5"


async def test_get_rules_filter_source_subscription(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.5", source="local"))
    db.add(_make_rule("203.0.113.6", source="subscription:1"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"source": "subscription"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["ip_address"] == "203.0.113.6"


async def test_get_rules_filter_source_all(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.5", source="local"))
    db.add(_make_rule("203.0.113.6", source="subscription:1"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"source": "all"})
    assert r.json()["total"] == 2


async def test_get_rules_filter_since(client):
    ac, db = client
    await _login(ac)
    past = _now().replace(year=2020)
    recent = _now()
    old_rule = _make_rule("203.0.113.5")
    old_rule.created_at = past
    old_rule.updated_at = past
    new_rule = _make_rule("203.0.113.6")
    new_rule.created_at = recent
    new_rule.updated_at = recent
    db.add(old_rule)
    db.add(new_rule)
    db.commit()
    r = await ac.get("/api/v1/rules", params={"since": "2025-01-01T00:00:00"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["ip_address"] == "203.0.113.6"


async def test_get_rules_filter_search_by_ip(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.45"))
    db.add(_make_rule("198.51.100.1"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"search": "203"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["ip_address"] == "203.0.113.45"


async def test_get_rules_filter_search_by_comment(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.45", comment="spinbot host"))
    db.add(_make_rule("198.51.100.1", comment="known good"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"search": "SPINBOT"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


async def test_get_rules_search_wildcards_escaped(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.1", comment="hello%world"))
    db.add(_make_rule("203.0.113.2", comment="other"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"search": "%"})
    assert r.status_code == 200
    # '%' must be escaped as a literal, so it only matches the row with '%' in comment
    assert r.json()["total"] == 1


async def test_get_rules_pagination_limit(client):
    ac, db = client
    await _login(ac)
    for i in range(5):
        db.add(_make_rule(f"203.0.113.{i + 1}"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


async def test_get_rules_pagination_offset(client):
    ac, db = client
    await _login(ac)
    for i in range(5):
        db.add(_make_rule(f"203.0.113.{i + 1}"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"limit": 2, "offset": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1  # only 1 left after offset 4


async def test_get_rules_total_reflects_unfiltered_count(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.1"))
    db.add(_make_rule("203.0.113.2"))
    db.add(_make_rule("203.0.113.3"))
    db.commit()
    r = await ac.get("/api/v1/rules", params={"limit": 1, "offset": 0})
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


# ── POST /rules ───────────────────────────────────────────────────────────────


async def test_post_rule_happy_path(client):
    ac, db = client
    await _login(ac)
    r = await ac.post(
        "/api/v1/rules",
        json={"ip_address": "203.0.113.45", "comment": "test"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["ip_address"] == "203.0.113.45"
    assert body["cidr_prefix"] == 32
    assert body["source"] == "local"
    assert body["comment"] == "test"
    assert body["id"] is not None


async def test_post_rule_with_cidr(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post(
        "/api/v1/rules",
        json={"ip_address": "203.0.113.0", "cidr_prefix": 24},
    )
    assert r.status_code == 201
    assert r.json()["cidr_prefix"] == 24


async def test_post_rule_rejects_loopback():
    pass  # covered by validation unit tests; routes call the same function


async def test_post_rule_rejects_private(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules", json={"ip_address": "192.168.1.100"})
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["error"] == "ip_not_blockable"
    assert body["detail"]["reason"] == "private"


async def test_post_rule_rejects_loopback_ip(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules", json={"ip_address": "127.0.0.1"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "loopback"


async def test_post_rule_rejects_link_local(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules", json={"ip_address": "169.254.1.1"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "link-local"


async def test_post_rule_rejects_invalid_ip(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules", json={"ip_address": "not-an-ip"})
    assert r.status_code == 422


async def test_post_rule_rejects_xbl_allowlist(client, monkeypatch):
    import xblp_common.validation as val_mod

    monkeypatch.setattr(val_mod, "_XBL_ALLOWLIST_PATH", __import__("pathlib").Path("/nonexistent"))
    # Inject the allowlist via monkeypatching load_xbl_allowlist result
    monkeypatch.setattr(val_mod, "load_xbl_allowlist", lambda: [("203.0.113.0", 24)])
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.45"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "xbox-live"


async def test_post_rule_conflict_returns_409(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="local"))
    db.commit()
    r = await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.10"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "rule_already_exists"


async def test_post_rule_subscription_same_ip_does_not_conflict(client):
    """Subscription rule with same IP/CIDR does not block a new local rule (Option A scope)."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    r = await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.10"})
    assert r.status_code == 201


async def test_post_rule_writes_audit_log(client):
    ac, db = client
    await _login(ac)
    await ac.post("/api/v1/rules", json={"ip_address": "203.0.113.45", "comment": "test"})
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.rule_added).first()
    assert entry is not None
    assert entry.target == "203.0.113.45/32"
    assert entry.undo_token is not None
    assert len(entry.undo_token) == 32  # uuid4().hex is 32 hex chars


# ── PATCH /rules/{id} ────────────────────────────────────────────────────────


async def test_patch_rule_happy_path(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", comment="old"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.patch(f"/api/v1/rules/{rule.id}", json={"comment": "new"})
    assert r.status_code == 200
    assert r.json()["comment"] == "new"


async def test_patch_rule_updates_confidence(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.patch(f"/api/v1/rules/{rule.id}", json={"confidence": "high"})
    assert r.status_code == 200
    assert r.json()["confidence"] == "high"


async def test_patch_rule_noop_field_set_but_unchanged(client):
    """PATCH with the same value as the current row: no audit entry, 200 returned."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", comment="existing"))
    db.commit()
    rule = db.query(Rule).first()
    db.expire_all()
    r = await ac.patch(f"/api/v1/rules/{rule.id}", json={"comment": "existing"})
    assert r.status_code == 200
    # No audit entry should have been written
    db.expire_all()
    entries = db.query(AuditLog).filter_by(event_type=EventType.rule_edited).all()
    assert len(entries) == 0


async def test_patch_rule_noop_vs_actual_change_both_covered(client):
    """One field unchanged, one field actually changed: only the change is recorded."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", comment="same"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.patch(
        f"/api/v1/rules/{rule.id}", json={"comment": "same", "confidence": "medium"}
    )
    assert r.status_code == 200
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.rule_edited).first()
    assert entry is not None
    assert "confidence" in entry.details["changes"]
    assert "comment" not in entry.details["changes"]


async def test_patch_rule_subscription_returns_403(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.patch(f"/api/v1/rules/{rule.id}", json={"comment": "x"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "subscription_rule_immutable"


async def test_patch_rule_missing_returns_404(client):
    ac, _ = client
    await _login(ac)
    r = await ac.patch("/api/v1/rules/9999", json={"comment": "x"})
    assert r.status_code == 404


async def test_patch_rule_writes_audit_with_undo_token(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", comment="old"))
    db.commit()
    rule = db.query(Rule).first()
    await ac.patch(f"/api/v1/rules/{rule.id}", json={"comment": "new"})
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.rule_edited).first()
    assert entry is not None
    assert entry.undo_token is not None


# ── DELETE /rules/{id} ───────────────────────────────────────────────────────


async def test_delete_rule_happy_path(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.delete(f"/api/v1/rules/{rule.id}")
    assert r.status_code == 204
    db.expire_all()
    assert db.query(Rule).count() == 0


async def test_delete_rule_subscription_returns_403(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.delete(f"/api/v1/rules/{rule.id}")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "subscription_rule_immutable"


async def test_delete_rule_missing_returns_404(client):
    ac, _ = client
    await _login(ac)
    r = await ac.delete("/api/v1/rules/9999")
    assert r.status_code == 404


async def test_delete_rule_writes_audit_with_undo_token(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10"))
    db.commit()
    rule = db.query(Rule).first()
    await ac.delete(f"/api/v1/rules/{rule.id}")
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.rule_removed).first()
    assert entry is not None
    assert entry.target == "203.0.113.10/32"
    assert entry.undo_token is not None


# ── POST /rules/{id}/promote ─────────────────────────────────────────────────


async def test_promote_subscription_to_local(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.post(f"/api/v1/rules/{rule.id}/promote")
    assert r.status_code == 204
    db.expire_all()
    updated = db.get(Rule, rule.id)
    assert updated.source == "local"
    assert updated.subscription_id is None


async def test_promote_local_rule_is_noop(client):
    """Promoting an already-local rule is 204 with no changes."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="local"))
    db.commit()
    rule = db.query(Rule).first()
    r = await ac.post(f"/api/v1/rules/{rule.id}/promote")
    assert r.status_code == 204
    db.expire_all()
    # Rule is still local, no audit written
    entries = db.query(AuditLog).filter_by(event_type=EventType.rule_edited).all()
    assert len(entries) == 0


async def test_promote_missing_rule_returns_404(client):
    ac, _ = client
    await _login(ac)
    r = await ac.post("/api/v1/rules/9999/promote")
    assert r.status_code == 404


async def test_promote_writes_audit_with_undo_token(client):
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    rule = db.query(Rule).first()
    await ac.post(f"/api/v1/rules/{rule.id}/promote")
    db.expire_all()
    entry = db.query(AuditLog).filter_by(event_type=EventType.rule_edited).first()
    assert entry is not None
    assert entry.undo_token is not None
    assert entry.details["promoted"] is True


async def test_promote_conflict_with_existing_local_returns_409(client):
    """Promoting a subscription rule when a local rule already exists for the same IP/CIDR."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="local"))
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    sub_rule = db.query(Rule).filter_by(source="subscription:1").first()
    r = await ac.post(f"/api/v1/rules/{sub_rule.id}/promote")
    assert r.status_code == 409


# ── Post-promote rule is deletable as local ───────────────────────────────────


async def test_promoted_rule_can_be_deleted(client):
    """After promotion, DELETE succeeds (no longer subscription-guarded)."""
    ac, db = client
    await _login(ac)
    db.add(_make_rule("203.0.113.10", source="subscription:1"))
    db.commit()
    rule = db.query(Rule).first()
    r1 = await ac.post(f"/api/v1/rules/{rule.id}/promote")
    assert r1.status_code == 204
    r2 = await ac.delete(f"/api/v1/rules/{rule.id}")
    assert r2.status_code == 204


# ── Startup reconcile ─────────────────────────────────────────────────────────


async def test_startup_reconcile_applies_db_rules_to_nft():
    """Rules in DB before startup are applied to the (mocked) nft blocklist during lifespan."""
    from unittest.mock import MagicMock, patch

    engine = _make_engine()
    with Session(engine) as db:
        db.add(_make_rule("203.0.113.10", cidr=32))
        db.commit()

    mock_nft = MagicMock()
    mock_nft.list_blocklist.return_value = []

    settings = _make_settings(nft_enabled=True)
    app = create_app(settings=settings, engine=engine)

    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")
    await ac.__aenter__()
    ctx = app.router.lifespan_context(app)
    with patch("xblp_api.app._init_nft_manager", return_value=mock_nft):
        await ctx.__aenter__()
    try:
        mock_nft.apply_diff.assert_called_once_with(
            "blocklist", [("203.0.113.10", 32)], []
        )
    finally:
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)
