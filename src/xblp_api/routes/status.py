"""Status endpoint (see DESIGN.md §10.1).

GET /api/v1/status — system health, active profile, capture state, rule counts.

Requires a valid session and must_change_password=false.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from xblp_api.auth.dependencies import require_password_changed
from xblp_common.models import PeerSnapshot, Rule, RuntimeState, User
from xblp_common.schemas import RulesCount, StatusResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/status", tags=["status"])

# How recent a peer_snapshots row must be to count as 'active'.
_CAPTURE_ACTIVE_THRESHOLD_SECONDS = 3


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("xboxlive-protect")
    except Exception:
        return "0.1.0"


def _capture_status(latest_ts: datetime | None) -> str:
    if latest_ts is None:
        return "missing"
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - latest_ts).total_seconds()
    return "active" if age <= _CAPTURE_ACTIVE_THRESHOLD_SECONDS else "stale"


# ── GET /status ───────────────────────────────────────────────────────────────


@router.get("", response_model=StatusResponse)
async def get_status(
    request: Request,
    _user: User = Depends(require_password_changed),
) -> StatusResponse:
    """Return system health and capture state.

    ``capture_status``:
      'active'  — latest peer_snapshots row is within 3 seconds
      'stale'   — latest row exists but is older than 3 seconds
      'missing' — no peer_snapshots rows exist

    ``blocklist_size`` is the live count from nftables (0 on Windows dev
    where nftables is disabled).
    """
    db: OrmSession = request.state.db

    # ── Capture last seen ─────────────────────────────────────────────────────
    latest_ts: datetime | None = db.execute(
        select(func.max(PeerSnapshot.captured_at))
    ).scalar_one()

    # ── Active profile (written by capture daemon at startup) ─────────────────
    profile_state = db.get(RuntimeState, "active_profile")
    active_profile: str | None = profile_state.value if profile_state else None

    # ── Rules counts ──────────────────────────────────────────────────────────
    total_rules = db.execute(select(func.count(Rule.id))).scalar_one()
    local_rules = db.execute(
        select(func.count(Rule.id)).where(Rule.source == "local")
    ).scalar_one()
    subscription_rules = total_rules - local_rules

    # ── Blocklist size (live nft query; 0 when nftables disabled) ─────────────
    nft_manager = request.app.state.nft_manager
    try:
        blocklist_size = len(nft_manager.list_blocklist())
    except Exception:
        blocklist_size = 0

    return StatusResponse(
        version=_get_version(),
        uptime_seconds=int(time.monotonic() - request.app.state.start_time),
        active_profile=active_profile,
        capture_status=_capture_status(latest_ts),
        capture_last_seen=latest_ts,
        rules_count=RulesCount(
            total=total_rules,
            local=local_rules,
            subscription=subscription_rules,
        ),
        blocklist_size=blocklist_size,
    )
