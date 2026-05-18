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


# ── POST /rules ───────────────────────────────────────────────────────────────


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreate,
    request: Request,
    _user: User = Depends(require_password_changed),
) -> RuleResponse:
    """Add a local block rule.

    Validates the IP against RFC 1918, loopback, link-local, the Xbox Live
    allowlist (inert until Phase 3), and the device's detected gateway/bridge
    IP.  Returns 422 with ``{"error": "ip_not_blockable", "reason": "<reason>"}``
    on any rejection.

    Returns 409 if a local rule for the same ``(ip_address, cidr_prefix)``
    already exists.  A subscription rule for the same IP does not conflict —
    both can coexist; see docs/api-rules.md for the uniqueness model.

    Writes a ``rule_added`` audit entry with a ``undo_token`` for Phase 4 undo.
    Calls ``reconcile_blocklist`` to project the new rule into nftables.
    """
    db = _get_db(request)
    nft = _get_nft(request)

    ok, reason = is_ip_blockable(body.ip_address, body.cidr_prefix)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "ip_not_blockable", "reason": reason},
        )

    now = _now()
    rule = Rule(
        ip_address=body.ip_address,
        cidr_prefix=body.cidr_prefix,
        source="local",
        comment=body.comment,
        confidence=body.confidence,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "rule_already_exists",
                "message": "A local rule for this (ip_address, cidr_prefix) already exists.",
            },
        )

    undo_token = uuid.uuid4().hex
    _write_rule_audit(
        db,
        EventType.rule_added,
        target=f"{body.ip_address}/{body.cidr_prefix}",
        undo_token=undo_token,
        details={
            "id": rule.id,
            "comment": body.comment,
            "confidence": str(body.confidence) if body.confidence is not None else None,
        },
    )
    db.commit()

    reconcile_blocklist(db, nft)

    db.refresh(rule)
    log.info("rule_added", ip=body.ip_address, cidr=body.cidr_prefix, id=rule.id)
    return RuleResponse.model_validate(rule)


# ── PATCH /rules/{rule_id} ────────────────────────────────────────────────────


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    request: Request,
    _user: User = Depends(require_password_changed),
) -> RuleResponse:
    """Edit a local rule's comment and/or confidence.

    ``ip_address`` and ``cidr_prefix`` are immutable (they are the identity of
    the rule — changing them requires delete + recreate).  Only fields present
    in the request body are updated; omitting a field leaves it unchanged.
    Setting a field to ``null`` explicitly clears it.

    Returns 403 for subscription rules.  Returns 404 if the rule does not exist.
    Returns 200 with the updated rule.  If the provided values are identical to
    the current row no audit entry is written and 200 is returned immediately.

    PATCH never changes ip_address/cidr_prefix, so nftables state is unchanged;
    reconcile_blocklist is not called.
    """
    db = _get_db(request)

    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if rule.source != "local":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_SUBSCRIPTION_RULE_IMMUTABLE
        )

    # Determine which provided fields actually differ from the current row.
    # model_fields_set tells us what the caller sent; we then compare values.
    changed: dict[str, object] = {}
    if "comment" in body.model_fields_set and body.comment != rule.comment:
        changed["comment"] = body.comment
    if "confidence" in body.model_fields_set and body.confidence != rule.confidence:
        changed["confidence"] = body.confidence

    if not changed:
        return RuleResponse.model_validate(rule)

    now = _now()
    for field, value in changed.items():
        setattr(rule, field, value)
    rule.updated_at = now

    undo_token = uuid.uuid4().hex
    _write_rule_audit(
        db,
        EventType.rule_edited,
        target=f"{rule.ip_address}/{rule.cidr_prefix}",
        undo_token=undo_token,
        details={
            "id": rule_id,
            "changes": {k: str(v) if v is not None else None for k, v in changed.items()},
        },
    )
    db.commit()

    db.refresh(rule)
    log.info("rule_edited", id=rule_id, changes=list(changed))
    return RuleResponse.model_validate(rule)


# ── DELETE /rules/{rule_id} ───────────────────────────────────────────────────


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    request: Request,
    _user: User = Depends(require_password_changed),
) -> None:
    """Remove a local block rule.

    Returns 403 for subscription rules (use POST /rules/{id}/promote first if
    you want to delete a subscription rule by converting it to local first).
    Returns 404 if the rule does not exist.

    Writes a ``rule_removed`` audit entry with a ``undo_token``.
    Calls ``reconcile_blocklist`` to remove the IP from nftables.
    """
    db = _get_db(request)
    nft = _get_nft(request)

    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if rule.source != "local":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_SUBSCRIPTION_RULE_IMMUTABLE
        )

    ip = rule.ip_address
    cidr = rule.cidr_prefix

    undo_token = uuid.uuid4().hex
    _write_rule_audit(
        db,
        EventType.rule_removed,
        target=f"{ip}/{cidr}",
        undo_token=undo_token,
        details={"id": rule_id, "comment": rule.comment},
    )
    db.delete(rule)
    db.commit()

    reconcile_blocklist(db, nft)
    log.info("rule_removed", ip=ip, cidr=cidr, id=rule_id)
