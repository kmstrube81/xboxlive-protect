"""Rules endpoints (see DESIGN.md §10.2).

GET    /api/v1/rules               list with filters + pagination
POST   /api/v1/rules               add local rule
PATCH  /api/v1/rules/{id}          edit local rule (comment, confidence)
DELETE /api/v1/rules/{id}          remove local rule
POST   /api/v1/rules/{id}/promote  promote subscription rule → local

All endpoints require a valid session and must_change_password=false.
Route handlers are intentionally thin: validate, mutate DB, reconcile nft,
write audit entry, return.  Reconciler is the single point of truth for
DB→nft projection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from xblp_api.auth.dependencies import require_password_changed
from xblp_common.models import AuditLog, EventType, Rule, User
from xblp_common.nft import NftManager, NoopNftManager
from xblp_common.reconcile import reconcile_blocklist
from xblp_common.schemas import RuleCreate, RuleList, RuleResponse, RuleUpdate
from xblp_common.validation import is_ip_blockable

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])

_SUBSCRIPTION_RULE_IMMUTABLE = {
    "error": "subscription_rule_immutable",
    "message": (
        "Subscription rules are read-only. "
        "Use POST /rules/{id}/promote to convert to a local rule first."
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_db(request: Request) -> DbSession:
    return request.state.db  # type: ignore[no-any-return]


def _get_nft(request: Request) -> NftManager | NoopNftManager:
    return request.app.state.nft_manager  # type: ignore[no-any-return]


def _write_rule_audit(
    db: DbSession,
    event_type: EventType,
    target: str,
    undo_token: str,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            timestamp=_now(),
            event_type=event_type,
            actor="user",
            target=target,
            details=details,
            undo_token=undo_token,
        )
    )


# ── GET /rules ────────────────────────────────────────────────────────────────


@router.get("", response_model=RuleList)
async def list_rules(
    request: Request,
    source: Annotated[Literal["local", "subscription", "all"], Query()] = "all",
    since: datetime | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _user: User = Depends(require_password_changed),
) -> RuleList:
    """List rules with optional filters and cursor-free pagination.

    ``source``: ``local`` | ``subscription`` | ``all`` (default).
    ``since``: ISO 8601 datetime; returns rules where ``created_at > since``.
    ``search``: case-insensitive substring match on ``ip_address`` or ``comment``.
    ``limit`` / ``offset``: page size (max 1000) and starting offset.
    Response includes ``total`` (count before pagination) for building pagers.
    """
    db = _get_db(request)
    q = db.query(Rule)

    if source == "local":
        q = q.filter(Rule.source == "local")
    elif source == "subscription":
        q = q.filter(Rule.source.startswith("subscription:"))

    if since is not None:
        naive_since = since.replace(tzinfo=None) if since.tzinfo else since
        q = q.filter(Rule.created_at > naive_since)

    if search is not None:
        # Escape LIKE metacharacters to prevent wildcard injection
        escaped = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        q = q.filter(
            or_(
                func.lower(Rule.ip_address).like(pattern, escape="\\"),
                func.lower(Rule.comment).like(pattern, escape="\\"),
            )
        )

    total = q.count()
    items = q.order_by(Rule.created_at.desc()).offset(offset).limit(limit).all()

    return RuleList(
        total=total,
        items=[RuleResponse.model_validate(r) for r in items],
        limit=limit,
        offset=offset,
    )
