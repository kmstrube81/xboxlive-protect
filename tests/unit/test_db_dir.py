"""Unit tests for API startup behaviour (directory creation, memory DB)."""

import pytest

from xblp_api.config import Settings


def _make_settings(**overrides) -> Settings:
    base = dict(
        cookie_secure=False,
        nft_enabled=False,
        tls_enabled=False,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.mark.unit
async def test_startup_creates_missing_db_parent_dir(tmp_path):
    """Lifespan creates the DB parent directory when it doesn't exist."""
    from sqlalchemy import create_engine as sa_create_engine, event
    from sqlalchemy.pool import StaticPool

    from xblp_api.app import create_app
    from xblp_common.migrations import create_tables

    # Two levels deep so neither directory pre-exists.
    db_file = tmp_path / "subdir" / "state.db"
    assert not db_file.parent.exists()

    settings = _make_settings(db_path=str(db_file))

    eng = sa_create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(eng, "connect", lambda conn, _: conn.cursor().execute("PRAGMA foreign_keys=ON"))

    app = create_app(settings=settings, engine=eng)
    async with app.router.lifespan_context(app):
        assert db_file.parent.exists()


@pytest.mark.unit
async def test_startup_memory_db_does_not_create_directory(tmp_path, monkeypatch):
    """A :memory: db_path must not attempt any filesystem mkdir."""
    from xblp_api.app import _ensure_db_dir

    created: list[str] = []

    # Patch Path.mkdir to detect any unexpected calls.
    original_mkdir = __import__("pathlib").Path.mkdir

    def _spy_mkdir(self, *args, **kwargs):
        created.append(str(self))
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.mkdir", _spy_mkdir)

    settings = _make_settings(db_path=":memory:")
    _ensure_db_dir(settings)

    assert created == [], f"mkdir was called unexpectedly: {created}"
