"""FastAPI dependency functions for session/user resolution (see DESIGN.md §6.3, §6.4).

Dependency chain:
  current_session  →  current_user  →  require_password_changed

current_session: reads cookie, returns UserSession | None (never 401s —
  that is the caller's responsibility so that /auth/login can be unauthenticated).
current_user: requires a valid session, raises 401 otherwise.
require_password_changed: requires must_change_password == False, raises 403
  with a stable error shape the UI can branch on.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from xblp_api.auth.sessions import get_session, slide_session
from xblp_api.config import Settings, get_settings
from xblp_common.models import User, UserSession

_COOKIE_NAME = "xblp_session"

_PASSWORD_CHANGE_REQUIRED = {
    "error": "password_change_required",
    "message": "Password must be changed before accessing this endpoint.",
}


def current_session(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> UserSession | None:
    """Return the active session attached by SessionMiddleware, if any."""
    return getattr(request.state, "session", None)


def current_user(
    session: UserSession | None = Depends(current_session),
) -> User:
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session.user


def require_password_changed(
    user: User = Depends(current_user),
) -> User:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_PASSWORD_CHANGE_REQUIRED,
        )
    return user
