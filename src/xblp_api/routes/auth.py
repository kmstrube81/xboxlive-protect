"""Auth endpoints (see DESIGN.md §10.8, §6.3).

POST /api/v1/auth/login    — authenticate, set session cookie
POST /api/v1/auth/logout   — revoke current session, clear cookie
POST /api/v1/auth/password — change password (requires login; exempt from
                             forced-change gate so users can actually change it)
GET  /api/v1/auth/me       — return current user info

All endpoints write to audit_log. Failed logins are logged but never throttled
(see DESIGN.md §6.3).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

import structlog

from xblp_api.auth.dependencies import current_user
from xblp_api.auth.hashing import hash_password, verify_password
from xblp_api.auth.sessions import (
    create_session,
    revoke_all_for_user,
    revoke_session,
)
from xblp_api.config import Settings, settings_from_app
from xblp_common.models import AuditLog, EventType, User, UserSession

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_COOKIE_NAME = "xblp_session"


# ── Request / response schemas ────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class MeResponse(BaseModel):
    username: str
    must_change_password: bool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_db(request: Request) -> DbSession:
    return request.state.db  # type: ignore[no-any-return]


def _write_audit(
    db: DbSession,
    event_type: EventType,
    actor: str,
    target: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            timestamp=_now(),
            event_type=event_type,
            actor=actor,
            target=target,
            details=details,
        )
    )


def _set_session_cookie(response: Response, session_id: str, settings: Settings) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        max_age=settings.session_lifetime_days * 86400,
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(settings_from_app),
) -> MeResponse:
    db: DbSession = _get_db(request)
    ua = request.headers.get("user-agent")
    client_ip = request.client.host if request.client else None

    user = db.query(User).filter(User.username == body.username).first()

    if user is None or not verify_password(user.password_hash, body.password, settings):
        _write_audit(
            db,
            EventType.login_failed,
            actor="user",
            target=body.username,
            details={"ip": client_ip, "ua": ua, "reason": "bad_credentials"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    session = create_session(db, user.id, settings, user_agent=ua, ip=client_ip)
    _write_audit(
        db,
        EventType.login,
        actor="user",
        target=user.username,
        details={"ip": client_ip, "ua": ua},
    )
    db.commit()

    _set_session_cookie(response, session.id, settings)
    log.info("login", username=user.username, ip=client_ip)
    return MeResponse(username=user.username, must_change_password=user.must_change_password)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(settings_from_app),
    user: User = Depends(current_user),
) -> None:
    db: DbSession = _get_db(request)
    session: UserSession | None = getattr(request.state, "session", None)
    if session is not None:
        revoke_session(db, session)
    _write_audit(db, EventType.logout, actor="user", target=user.username)
    db.commit()
    _clear_session_cookie(response, settings)
    log.info("logout", username=user.username)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    settings: Settings = Depends(settings_from_app),
    user: User = Depends(current_user),
) -> None:
    db: DbSession = _get_db(request)

    if not verify_password(user.password_hash, body.old_password, settings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )

    user.password_hash = hash_password(body.new_password, settings)
    user.must_change_password = False
    user.password_changed_at = _now()

    current_session: UserSession | None = getattr(request.state, "session", None)
    current_session_id = current_session.id if current_session else None
    revoke_all_for_user(db, user.id, except_session_id=current_session_id)

    _write_audit(db, EventType.password_changed, actor="user", target=user.username)
    db.commit()
    log.info("password_changed", username=user.username)


@router.get("/me", status_code=status.HTTP_200_OK)
async def me(
    user: User = Depends(current_user),
) -> MeResponse:
    return MeResponse(username=user.username, must_change_password=user.must_change_password)
