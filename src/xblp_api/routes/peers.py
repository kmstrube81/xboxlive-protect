"""Peers endpoints (see DESIGN.md §10.1).

GET /api/v1/peers         snapshot — latest peer batch from peer_snapshots
GET /api/v1/peers/stream  Server-Sent Events at 1 Hz

Both require a valid session and must_change_password=false.
"""

from __future__ import annotations

import asyncio
import json

import anyio
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from xblp_api.auth.dependencies import require_password_changed
from xblp_common.models import PeerSnapshot, User
from xblp_common.schemas import PeerSnapshotItem, PeersResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/peers", tags=["peers"])

_MAX_SSE_CLIENTS = 10


# ── Helpers ───────────────────────────────────────────────────────────────────


def _query_latest_snapshot(db: OrmSession) -> PeersResponse:
    """Return the latest peer batch using an existing ORM session."""
    latest_ts = db.execute(select(func.max(PeerSnapshot.captured_at))).scalar_one()
    if latest_ts is None:
        return PeersResponse(captured_at=None, peers=[], count=0)
    rows = db.execute(
        select(PeerSnapshot).where(PeerSnapshot.captured_at == latest_ts)
    ).scalars().all()
    items = [PeerSnapshotItem.model_validate(r) for r in rows]
    return PeersResponse(captured_at=latest_ts, peers=items, count=len(items))


def _query_latest_snapshot_fresh(engine: Engine) -> PeersResponse:
    """Return the latest peer batch via a fresh ORM session (used by SSE)."""
    with OrmSession(engine) as db:
        return _query_latest_snapshot(db)


# ── GET /peers ────────────────────────────────────────────────────────────────


@router.get("", response_model=PeersResponse)
async def get_peers(
    request: Request,
    _user: User = Depends(require_password_changed),
) -> PeersResponse:
    """Return the most recent batch of peer_snapshots rows.

    All rows sharing ``MAX(captured_at)`` constitute one capture-daemon tick.
    Returns 200 with empty peers list and ``captured_at: null`` if the capture
    daemon has not yet written any snapshots.
    """
    db: OrmSession = request.state.db
    return _query_latest_snapshot(db)


# ── GET /peers/stream ─────────────────────────────────────────────────────────


@router.get("/stream")
async def stream_peers(
    request: Request,
    _user: User = Depends(require_password_changed),
) -> StreamingResponse:
    """Stream peer updates as Server-Sent Events at approximately 1 Hz.

    Each event carries the latest snapshot batch in the same JSON shape as
    GET /peers:

        event: peers
        data: {"captured_at": "...", "peers": [...], "count": N}

    Returns 503 if 10 SSE clients are already connected.  The UI is expected
    to have at most one stream open per user.

    On client disconnect the async generator exits; the counter on app.state
    is decremented in the finally block.
    """
    if request.app.state.sse_client_count >= _MAX_SSE_CLIENTS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "too_many_sse_clients",
                "message": f"Maximum {_MAX_SSE_CLIENTS} concurrent SSE clients reached.",
            },
        )

    engine: Engine = request.app.state.engine

    async def _generate() -> object:
        request.app.state.sse_client_count += 1
        log.info("sse client connected", total=request.app.state.sse_client_count)
        try:
            while True:
                # Fresh ORM session per tick — does not hold a connection
                # between yields (prevents pool starvation at high concurrency).
                snapshot = _query_latest_snapshot_fresh(engine)
                payload = snapshot.model_dump(mode="json")
                data = json.dumps(payload, default=str)
                yield f"event: peers\ndata: {data}\n\n"
                await anyio.sleep(1.0)
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            request.app.state.sse_client_count -= 1
            log.info("sse client disconnected", total=request.app.state.sse_client_count)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
