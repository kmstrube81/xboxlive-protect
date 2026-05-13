"""Server-side session management (see DESIGN.md §6.3).

Sessions are rows in the `sessions` table keyed by a 64-character hex string
(32 random bytes). Sliding expiration: each authenticated request extends
expires_at by the configured session lifetime.
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DbSession

from xblp_api.config import Settings
from xblp_common.models import UserSession


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_session(
    db: DbSession,
    user_id: int,
    settings: Settings,
    user_agent: str | None = None,
    ip: str | None = None,
) -> UserSession:
    now = _now()
    session = UserSession(
        id=secrets.token_hex(settings.session_id_bytes),
        user_id=user_id,
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(days=settings.session_lifetime_days),
        user_agent=user_agent,
        ip=ip,
    )
    db.add(session)
    db.flush()
    return session


def get_session(db: DbSession, session_id: str) -> UserSession | None:
    """Return the session if it exists and has not expired, else None."""
    session = db.get(UserSession, session_id)
    if session is None:
        return None
    if session.expires_at < _now():
        return None
    return session


def slide_session(db: DbSession, session: UserSession, settings: Settings) -> None:
    """Extend expiration and update last_used_at (sliding expiration)."""
    now = _now()
    session.last_used_at = now
    session.expires_at = now + timedelta(days=settings.session_lifetime_days)
    db.flush()


def revoke_session(db: DbSession, session: UserSession) -> None:
    db.delete(session)
    db.flush()


def revoke_all_for_user(db: DbSession, user_id: int, except_session_id: str | None = None) -> int:
    """Delete all sessions for *user_id*, optionally sparing one.

    Returns the number of sessions deleted.
    """
    query = db.query(UserSession).filter(UserSession.user_id == user_id)
    if except_session_id is not None:
        query = query.filter(UserSession.id != except_session_id)
    sessions = query.all()
    for s in sessions:
        db.delete(s)
    db.flush()
    return len(sessions)
