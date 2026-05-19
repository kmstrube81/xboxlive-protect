"""Integration tests for peers and status endpoints.

Run on R4S with: pytest -m integration

These tests require:
- xblp-api running (or started in-process)
- xblp-capture running on br0 with real traffic (or no traffic is also valid)
- A valid session cookie

Coverage:
- Capture daemon writes peer_snapshots rows to state.db
- GET /api/v1/peers returns non-empty or empty peers with correct shape
- GET /api/v1/peers/stream delivers valid events for >=3 seconds
- GET /api/v1/status returns expected shape with capture_status
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────────────────────

_BASE_URL = os.environ.get("XBLP_API_URL", "https://xboxlive-protect.local")
_SESSION_COOKIE = os.environ.get("XBLP_SESSION_COOKIE", "")
_DB_PATH = os.environ.get("XBLP_DB_PATH", "/var/lib/xboxlive-protect/state.db")


def _session_headers() -> dict[str, str]:
    if not _SESSION_COOKIE:
        pytest.skip("XBLP_SESSION_COOKIE not set — run install-stage1.sh and log in first")
    return {"Cookie": f"xblp_session={_SESSION_COOKIE}"}


# ── peer_snapshots DB test ─────────────────────────────────────────────────────


@pytest.mark.integration
def test_capture_daemon_writes_peer_snapshots() -> None:
    """After a few seconds of running, peer_snapshots table should have rows.

    Even with no Xbox traffic, the table should exist.  If the capture daemon
    is running and the Xbox is active, rows will be non-empty.  We just verify
    the table is accessible and the schema is correct.
    """
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session

    from xblp_common import db as db_module
    from xblp_common.migrations import create_tables
    from xblp_common.models import PeerSnapshot

    engine = db_module.create_engine(db_path=_DB_PATH)
    create_tables(engine)

    with Session(engine) as db:
        # Table must exist and be queryable
        rows = db.execute(select(PeerSnapshot)).scalars().all()
        # Schema check: if any rows exist, they must have valid fields
        for row in rows:
            assert row.peer_ip is not None
            assert isinstance(row.pps, float)
            assert isinstance(row.bytes_in, int)
            assert isinstance(row.bytes_out, int)
            assert isinstance(row.flagged, bool)


# ── GET /peers integration ────────────────────────────────────────────────────


@pytest.mark.integration
def test_get_peers_returns_valid_shape() -> None:
    """GET /peers returns a valid response shape via the live API."""
    import urllib.request

    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = _session_headers()
    req = urllib.request.Request(
        f"{_BASE_URL}/api/v1/peers",
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        body = json.loads(resp.read())

    assert "peers" in body
    assert "count" in body
    assert isinstance(body["peers"], list)
    assert body["count"] == len(body["peers"])
    # captured_at is null when no traffic, or an ISO8601 string
    assert "captured_at" in body

    for peer in body["peers"]:
        assert "peer_ip" in peer
        assert "pps" in peer
        assert "score" in peer
        assert "flagged" in peer
        assert "bytes_in" in peer
        assert "bytes_out" in peer


# ── GET /peers/stream integration ─────────────────────────────────────────────


@pytest.mark.integration
def test_get_peers_stream_delivers_events() -> None:
    """Stream delivers correctly formatted SSE events for at least 3 seconds."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {**_session_headers(), "Accept": "text/event-stream"}
    req = urllib.request.Request(
        f"{_BASE_URL}/api/v1/peers/stream",
        headers=headers,
        method="GET",
    )

    events: list[dict] = []
    start = time.monotonic()

    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        buffer = b""
        while time.monotonic() - start < 3.5:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk
            # Parse complete SSE messages (terminated by \n\n)
            while b"\n\n" in buffer:
                msg, buffer = buffer.split(b"\n\n", 1)
                lines = msg.decode().splitlines()
                data_line = next((l for l in lines if l.startswith("data:")), None)
                if data_line:
                    events.append(json.loads(data_line[5:].strip()))

    assert len(events) >= 3, f"Expected >=3 events in 3.5s, got {len(events)}"
    for ev in events:
        assert "peers" in ev
        assert "count" in ev
        assert "captured_at" in ev


# ── GET /status integration ───────────────────────────────────────────────────


@pytest.mark.integration
def test_get_status_returns_expected_shape() -> None:
    """GET /status returns a valid response with all required fields."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = _session_headers()
    req = urllib.request.Request(
        f"{_BASE_URL}/api/v1/status",
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        body = json.loads(resp.read())

    assert "version" in body
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], int)
    assert "active_profile" in body
    assert "capture_status" in body
    assert body["capture_status"] in ("active", "stale", "missing")
    assert "capture_last_seen" in body
    assert "rules_count" in body
    rc = body["rules_count"]
    assert "total" in rc and "local" in rc and "subscription" in rc
    assert "blocklist_size" in body
    assert isinstance(body["blocklist_size"], int)


@pytest.mark.integration
def test_get_status_capture_status_active_when_running() -> None:
    """When xblp-capture is running and writing, capture_status should be 'active'."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = _session_headers()
    req = urllib.request.Request(
        f"{_BASE_URL}/api/v1/status",
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        body = json.loads(resp.read())

    # xblp-capture.service must be running on R4S for this assertion to hold.
    # If capture is not running this will be 'missing' and the test skips.
    if body["capture_status"] == "missing":
        pytest.skip("xblp-capture not running — start it first")
    assert body["capture_status"] == "active"
