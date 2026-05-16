"""Unit tests for the startup writability probes (Windows-runnable).

_probe_state_dir_writable writes a single byte to a probe file in the
state directory and immediately removes it. On failure it logs an error
and calls sys.exit(1).

_probe_db_writable_via_real_insert opens a connection to the SQLite file
and attempts a CREATE / INSERT / DROP cycle. On OperationalError it logs
an error and calls sys.exit(1). This catches the case where the state
directory is xblp-owned but state.db itself is root-owned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xblp_api.app import _probe_db_writable_via_real_insert, _probe_state_dir_writable
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
def test_probe_succeeds_on_writable_dir(tmp_path: Path) -> None:
    """Probe completes without error on a normal writable directory."""
    settings = _make_settings(db_path=str(tmp_path / "state.db"))
    _probe_state_dir_writable(settings)
    # Probe file must be cleaned up.
    assert not (tmp_path / ".xblp_write_probe").exists()


@pytest.mark.unit
def test_probe_skipped_for_memory_db() -> None:
    """Probe is a no-op when db_path is :memory: (tests and Windows dev)."""
    settings = _make_settings(db_path=":memory:")
    # If the probe ran it would try to write to the parent of ':memory:',
    # which would raise — absence of SystemExit confirms early return.
    _probe_state_dir_writable(settings)


@pytest.mark.unit
def test_probe_exits_on_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe calls sys.exit(1) when the directory write fails."""
    settings = _make_settings(db_path=str(tmp_path / "state.db"))

    def _fail_write(self: Path, data: bytes) -> None:  # noqa: ARG001
        raise PermissionError("Permission denied: /var/lib/xboxlive-protect/.xblp_write_probe")

    monkeypatch.setattr(Path, "write_bytes", _fail_write)

    with pytest.raises(SystemExit) as exc_info:
        _probe_state_dir_writable(settings)
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_probe_exits_on_any_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError (not just PermissionError) also triggers exit(1)."""
    settings = _make_settings(db_path=str(tmp_path / "state.db"))

    def _fail_write(self: Path, data: bytes) -> None:  # noqa: ARG001
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "write_bytes", _fail_write)

    with pytest.raises(SystemExit) as exc_info:
        _probe_state_dir_writable(settings)
    assert exc_info.value.code == 1


# ── _probe_db_writable_via_real_insert ────────────────────────────────────────


@pytest.mark.unit
def test_db_probe_succeeds_on_writable_db(tmp_path: Path) -> None:
    """DB probe completes without error on a writable SQLite file."""
    from sqlalchemy import create_engine as sa_create_engine

    settings = _make_settings(db_path=str(tmp_path / "state.db"))
    engine = sa_create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    _probe_db_writable_via_real_insert(engine, settings)


@pytest.mark.unit
def test_db_probe_skipped_for_memory_db() -> None:
    """DB probe is a no-op when db_path is :memory:."""
    from unittest.mock import MagicMock

    settings = _make_settings(db_path=":memory:")
    engine = MagicMock()
    _probe_db_writable_via_real_insert(engine, settings)
    engine.connect.assert_not_called()


@pytest.mark.unit
def test_db_probe_exits_on_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB probe calls sys.exit(1) when the INSERT fails with OperationalError."""
    import contextlib
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy.exc import OperationalError

    settings = _make_settings(db_path=str(tmp_path / "state.db"))
    engine = sa_create_engine(f"sqlite:///{tmp_path / 'state.db'}")

    @contextlib.contextmanager
    def _failing_connect():
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            "attempt to write a readonly database", None, BaseException()
        )
        yield mock_conn

    monkeypatch.setattr(engine, "connect", _failing_connect)

    with pytest.raises(SystemExit) as exc_info:
        _probe_db_writable_via_real_insert(engine, settings)
    assert exc_info.value.code == 1
