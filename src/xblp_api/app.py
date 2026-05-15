"""FastAPI application factory (see DESIGN.md §4.2, §6.1, §15 Phase 2).

Lifespan startup sequence:
  1. Create database tables (create_all — matches Phase 1 migration approach).
  2. Apply the nftables ruleset if absent (skipped on non-Linux or when
     XBLP_NFT_ENABLED=false so Windows dev doesn't crash).
  3. Seed the default admin user if the users table is empty.

The app is constructed by create_app() so tests can pass a custom Settings
object and a pre-built SQLAlchemy engine without touching env vars or the
filesystem.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from xblp_api.auth.hashing import hash_password
from xblp_api.config import Settings
from xblp_api.middleware import SessionMiddleware
from xblp_api.routes.auth import router as auth_router
from xblp_common import db as db_module
from xblp_common.migrations import create_tables
from xblp_common.models import User

log = structlog.get_logger(__name__)

_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_PASSWORD = "xboxlive-protect"


def _ensure_db_dir(settings: Settings) -> None:
    """Create the parent directory of the SQLite DB file if it doesn't exist.

    Skipped for :memory: databases. The install script creates
    /var/lib/xboxlive-protect in production, but on a fresh dev box or a
    fresh R4S before the install script has run, the directory may not exist
    yet and SQLAlchemy's first connection attempt would crash.
    """
    if settings.db_path == ":memory:":
        return
    parent = Path(settings.db_path).parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        log.info("created db parent directory", path=str(parent))


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


def _ensure_tls_cert(settings: Settings) -> None:
    """Generate the self-signed TLS cert if not already present.

    Skipped on Windows (tls_enabled=False by default). On Linux the cert is
    written to tls_cert_path/tls_key_path before nginx can start — see the
    nginx.service.d/xblp.conf drop-in that orders nginx after this service.
    """
    if not settings.tls_enabled:
        log.info("tls_enabled=false, skipping cert bootstrap (expected on Windows dev)")
        return
    from xblp_api.tls import ensure_cert_exists

    ensure_cert_exists(Path(settings.tls_cert_path), Path(settings.tls_key_path))


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


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Override runtime settings. If None, reads from env via get_settings().
        engine: Override the SQLAlchemy engine. If None, creates one from settings.db_path.
                Pass a StaticPool in-memory engine in unit tests.
    """
    if settings is None:
        from xblp_api.config import get_settings

        settings = get_settings()

    if engine is None:
        engine = db_module.create_engine(db_path=settings.db_path)

    session_factory: sessionmaker = db_module.create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        log.info("xblp-api starting up")
        _ensure_db_dir(settings)  # type: ignore[arg-type]
        _ensure_tls_cert(settings)  # type: ignore[arg-type]
        create_tables(engine)  # type: ignore[arg-type]
        _apply_nft_ruleset(settings)  # type: ignore[arg-type]
        _seed_admin(session_factory, settings)  # type: ignore[arg-type]
        log.info("xblp-api startup complete")
        yield
        log.info("xblp-api shutting down")
        engine.dispose()  # type: ignore[union-attr]

    app = FastAPI(title="xboxlive-protect API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        SessionMiddleware,  # type: ignore[arg-type]
        session_factory=session_factory,
        settings=settings,
    )

    app.include_router(auth_router)

    return app
