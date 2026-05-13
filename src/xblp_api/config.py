"""Runtime configuration for xblp-api (see DESIGN.md §6.3, §4.1).

All settings are read from environment variables (or a .env file if present).
Defaults are chosen for production on the R4S. For Windows dev, set:

    XBLP_COOKIE_SECURE=false   -- allows the session cookie over plain HTTP
    XBLP_NFT_ENABLED=false     -- skips nftables bootstrap (nft not available)
    XBLP_DB_PATH=state.db      -- use a local file instead of /var/lib/...
"""

import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_on_linux = sys.platform == "linux"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XBLP_", env_file=".env", extra="ignore")

    # Server
    bind_host: str = Field("0.0.0.0", alias="XBLP_BIND_HOST")
    bind_port: int = Field(8080, alias="XBLP_BIND_PORT")

    # Database
    db_path: str = Field("/var/lib/xboxlive-protect/state.db", alias="XBLP_DB_PATH")

    # Session cookie
    cookie_secure: bool = Field(default_factory=lambda: _on_linux, alias="XBLP_COOKIE_SECURE")
    session_lifetime_days: int = Field(30, alias="XBLP_SESSION_LIFETIME_DAYS")
    session_id_bytes: int = Field(32, alias="XBLP_SESSION_ID_BYTES")

    # nftables bootstrap — disabled automatically on non-Linux hosts
    nft_enabled: bool = Field(default_factory=lambda: _on_linux, alias="XBLP_NFT_ENABLED")

    # argon2id parameters (OWASP recommended minimums)
    argon2_time_cost: int = Field(2, alias="XBLP_ARGON2_TIME_COST")
    argon2_memory_cost: int = Field(65536, alias="XBLP_ARGON2_MEMORY_COST")  # 64 MiB
    argon2_parallelism: int = Field(2, alias="XBLP_ARGON2_PARALLELISM")


def get_settings() -> Settings:
    return Settings()
