"""Runtime configuration for xblp-api (see DESIGN.md §6.3, §4.1).

All settings are read from environment variables with the XBLP_ prefix
(e.g. XBLP_BIND_PORT=9090). A .env file in the working directory is also
read if present.

For Windows dev, set:
    XBLP_COOKIE_SECURE=false   -- allows the session cookie over plain HTTP
    XBLP_NFT_ENABLED=false     -- skips nftables bootstrap (nft not available)
    XBLP_DB_PATH=state.db      -- use a local file instead of /var/lib/...
"""

from __future__ import annotations

import sys

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_linux() -> bool:
    return sys.platform == "linux"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XBLP_",
        env_file=".env",
        extra="ignore",
        # Allow construction via field name (e.g. Settings(db_path=...)) in addition
        # to via alias, which is useful in tests and create_app().
        populate_by_name=True,
    )

    # Server — loopback-only; LAN access goes through nginx on 443.
    # Set XBLP_BIND_HOST=0.0.0.0 in .env for Windows dev that needs LAN reach.
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080

    # Database
    db_path: str = "/var/lib/xboxlive-protect/state.db"

    # Session cookie
    cookie_secure: bool = Field(default_factory=_is_linux)
    session_lifetime_days: int = 30
    session_id_bytes: int = 32

    # nftables bootstrap — disabled automatically on non-Linux hosts
    nft_enabled: bool = Field(default_factory=_is_linux)

    # TLS — self-signed cert generated at first daemon start (DESIGN.md §6.1)
    # Disabled automatically on non-Linux hosts (no nginx, no cert needed).
    tls_enabled: bool = Field(default_factory=_is_linux)
    tls_cert_path: str = "/var/lib/xboxlive-protect/cert.pem"
    tls_key_path: str = "/var/lib/xboxlive-protect/key.pem"

    # argon2id parameters (OWASP recommended minimums)
    argon2_time_cost: int = 2
    argon2_memory_cost: int = 65536  # 64 MiB
    argon2_parallelism: int = 2

    # UI static assets — path to ui/dist produced by `npm run build`.
    # FastAPI mounts StaticFiles here with html=True for SPA fallback.
    # When the path doesn't exist (no build yet) the daemon starts cleanly
    # and logs a warning; api/v1/* routes are unaffected.
    ui_dist_path: str = "/opt/xboxlive-protect/ui/dist"


def get_settings() -> Settings:
    return Settings()


def settings_from_app(request: Request) -> Settings:
    """FastAPI dependency that reads Settings from app.state (set by create_app).

    Using app.state rather than calling Settings() allows tests (and any code
    that calls create_app with explicit settings) to control configuration
    without touching environment variables.
    """
    return request.app.state.settings  # type: ignore[no-any-return]
