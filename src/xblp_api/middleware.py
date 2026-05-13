"""Session cookie middleware (see DESIGN.md §6.3).

Reads the xblp_session cookie on every request. If the cookie maps to a valid,
unexpired session row, attaches it to request.state.session and slides the
expiration. Never returns 401 — that is the auth dependency's job, so endpoints
that don't require authentication (e.g. /auth/login) work normally.

Also attaches a database session to request.state.db so route handlers and
dependencies can share a single transaction per request.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from sqlalchemy.orm import Session as DbSession, sessionmaker

from xblp_api.auth.sessions import get_session, slide_session
from xblp_api.config import Settings

_COOKIE_NAME = "xblp_session"


class SessionMiddleware:
    """ASGI middleware that resolves the session cookie and provides a DB session."""

    def __init__(
        self,
        app: Callable[[dict, Callable, Callable], Awaitable[None]],
        session_factory: sessionmaker[DbSession],
        settings: Settings,
    ) -> None:
        self.app = app
        self.session_factory = session_factory
        self.settings = settings

    async def __call__(
        self,
        scope: dict,
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        db: DbSession = self.session_factory()
        request.state.db = db
        request.state.session = None

        try:
            session_id = request.cookies.get(_COOKIE_NAME)
            if session_id:
                db_session = get_session(db, session_id)
                if db_session is not None:
                    slide_session(db, db_session, self.settings)
                    db.commit()
                    request.state.session = db_session
            await self.app(scope, receive, send)
        finally:
            db.close()
