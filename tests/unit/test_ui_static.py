"""Unit tests for the StaticFiles mount and SPA fallback (Phase 2 Stage 4).

Coverage:
- When XBLP_UI_DIST_PATH points at a directory containing index.html:
  - GET / returns the HTML (status 200, Content-Type text/html)
  - GET /<any/spa/path> returns index.html (SPA fallback)
  - GET /api/v1/auth/me returns JSON 401, NOT index.html
  - GET /api/v1/<typo> returns JSON 404 (the /api/v1 catch-all route)
- When XBLP_UI_DIST_PATH doesn't exist:
  - The daemon starts cleanly (no exception during create_app)
  - GET /api/v1/auth/me still returns JSON 401
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as sa_create_engine, event
from sqlalchemy.pool import StaticPool

from xblp_api.app import create_app
from xblp_api.config import Settings
from xblp_common.migrations import create_tables

pytestmark = pytest.mark.unit


# ── Helpers ───────────────────────────────────────────────────────────────────


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


async def _build_client(settings: Settings) -> tuple[AsyncClient, object]:
    engine = _make_engine()
    app = create_app(settings=settings, engine=engine)
    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")
    await ac.__aenter__()
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    return ac, ctx


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def ui_dist_dir():
    """Temp directory acting as ui/dist with a minimal index.html."""
    with tempfile.TemporaryDirectory() as tmpdir:
        index = Path(tmpdir) / "index.html"
        index.write_text("<!doctype html><html><body>xblp</body></html>", encoding="utf-8")
        yield tmpdir


@pytest_asyncio.fixture
async def client_with_ui(ui_dist_dir: str):
    settings = _make_settings(ui_dist_path=ui_dist_dir)
    ac, ctx = await _build_client(settings)
    try:
        yield ac
    finally:
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def client_no_ui():
    settings = _make_settings(ui_dist_path="/nonexistent/ui/dist")
    ac, ctx = await _build_client(settings)
    try:
        yield ac
    finally:
        await ctx.__aexit__(None, None, None)
        await ac.__aexit__(None, None, None)


# ── Tests — UI present ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_serves_index_html(client_with_ui: AsyncClient):
    r = await client_with_ui.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "xblp" in r.text


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_html_for_unknown_path(client_with_ui: AsyncClient):
    r = await client_with_ui.get("/dashboard/some/deep/path")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_api_route_not_shadowed_by_static_mount(client_with_ui: AsyncClient):
    """GET /api/v1/auth/me returns 401 JSON, not index.html."""
    r = await client_with_ui.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "detail" in body


@pytest.mark.asyncio
async def test_api_catch_all_returns_json_404(client_with_ui: AsyncClient):
    """Malformed /api/v1 paths return JSON 404, not index.html."""
    r = await client_with_ui.get("/api/v1/this-does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "detail" in body
    assert "not found" in body["detail"].lower()


# ── Tests — UI absent ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daemon_starts_cleanly_without_ui_dist(client_no_ui: AsyncClient):
    """App starts and serves API even when ui_dist_path doesn't exist."""
    r = await client_no_ui.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_api_catch_all_still_works_without_ui_dist(client_no_ui: AsyncClient):
    r = await client_no_ui.get("/api/v1/this-does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
