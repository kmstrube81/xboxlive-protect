"""FastAPI application factory (see DESIGN.md §4.2, §6.1, §15 Phase 2).

Lifespan startup sequence:
  1. Create database tables (create_all — matches Phase 1 migration approach).
  2. Apply the nftables ruleset if absent (skipped on non-Linux or when
     XBLP_NFT_ENABLED=false so Windows dev doesn't crash).
  3. Seed the default admin user if the users table is empty.

The app is constructed by create_app() so tests can pass a custom settings
object without touching env vars or touching the filesystem.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import structlog
from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from xblp_api.config import Settings
from xblp_api.auth.hashing import hash_password
from xblp_api.middleware import SessionMiddleware
from xblp_api.routes.auth import router as auth_router
from xblp_common import db as db_module
from xblp_common.migrations import create_tables
from xblp_common.models import User

log = structlog.get_logger(__name__)

_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_PASSWORD = "xboxlive-protect"


def _seed_admin(session_factory: sessionmaker, settings: Settings) -> None:
    """Create the default admin user if the users table is empty. Idempotent."""
    with session_factory() as db:
        if db.query(User).count() == 0:
            admin = User(
                username=_DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(_DEFAULT_ADMIN_PASSWORD, settings),
                must_change_password=True,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(admin)
            db.commit()
            log.info("default admin user seeded", username=_DEFAULT_ADMIN_USERNAME)
        else:
            log.debug("users table not empty, skipping admin seed")


def _apply_nft_ruleset(settings: Settings) -> None:
    """Install the nftables ruleset if not already present.

    Skipped gracefully when NFT_ENABLED is False (Windows dev) or when nft is
    not on the PATH. Either condition just logs a warning.
    """
    if not settings.nft_enabled:
        log.warning("nft_enabled=false, skipping ruleset bootstrap (expected on Windows dev)")
        return

    try:
        from xblp_common.nft import NftError, NftManager

        mgr = NftManager()
        mgr.apply_initial_ruleset()
    except FileNotFoundError:
        log.warning("nft binary not found, skipping ruleset bootstrap")
    except NftError as exc:
        log.error("nft ruleset bootstrap failed", error=str(exc))


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        from xblp_api.config import get_settings

        settings = get_settings()

    engine = db_module.create_engine(db_path=settings.db_path)
    session_factory: sessionmaker = db_module.create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        log.info("xblp-api starting up")
        create_tables(engine)
        _apply_nft_ruleset(settings)
        _seed_admin(session_factory, settings)
        log.info("xblp-api startup complete")
        yield
        log.info("xblp-api shutting down")
        engine.dispose()

    app = FastAPI(title="xboxlive-protect API", version="0.1.0", lifespan=lifespan)

    # Raw ASGI middleware; added after app construction so it wraps everything.
    app.add_middleware(
        SessionMiddleware,  # type: ignore[arg-type]
        session_factory=session_factory,
        settings=settings,
    )

    app.include_router(auth_router)

    return app
