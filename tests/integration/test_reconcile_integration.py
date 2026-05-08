"""Integration tests for reconcile_blocklist — require Linux, root, and nft.

Run with:
    sudo pytest -m integration

Automatically skipped on non-Linux platforms, when the nft binary is absent,
or when not running as root.  Uses the table name ``xblp_reconcile_test`` to
avoid any interaction with a production ``xblp`` install.
"""

from __future__ import annotations

import contextlib
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xblp_common.models import Rule
from xblp_common.nft import NftError, NftManager
from xblp_common.reconcile import reconcile_blocklist

pytestmark = [pytest.mark.integration, pytest.mark.linux]

_TEST_TABLE = "xblp_reconcile_test"


def _skip_reason() -> str | None:
    if platform.system() != "Linux":
        return "nftables integration tests require Linux"
    if not shutil.which("nft") and not Path("/usr/sbin/nft").exists():
        return "nft binary not found"
    # os.geteuid is POSIX-only; only reachable here when system is Linux
    import os

    if os.geteuid() != 0:
        return "nftables integration tests require root (sudo pytest -m integration)"
    return None


_reason = _skip_reason()
if _reason:
    pytest.skip(_reason, allow_module_level=True)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_rule(ip: str, cidr: int = 32, source: str = "local") -> Rule:
    now = _now()
    return Rule(ip_address=ip, cidr_prefix=cidr, source=source, created_at=now, updated_at=now)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mgr() -> NftManager:  # type: ignore[return]  # yield in try/finally
    m = NftManager(table=_TEST_TABLE)
    m.apply_initial_ruleset()
    try:
        yield m
    finally:
        with contextlib.suppress(NftError):
            m.remove_ruleset()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_reconcile_adds_db_rules_to_empty_blocklist(mgr: NftManager, db_session):  # type: ignore[no-untyped-def]
    db_session.add(_make_rule("192.0.2.1"))
    db_session.add(_make_rule("192.0.2.0", 24))
    db_session.flush()
    result = reconcile_blocklist(db_session, mgr)
    assert set(result.added) == {("192.0.2.1", 32), ("192.0.2.0", 24)}
    live = mgr.list_blocklist()
    assert ("192.0.2.1", 32) in live
    assert ("192.0.2.0", 24) in live


def test_reconcile_idempotent(mgr: NftManager, db_session):  # type: ignore[no-untyped-def]
    db_session.add(_make_rule("198.51.100.1"))
    db_session.flush()
    reconcile_blocklist(db_session, mgr)
    result = reconcile_blocklist(db_session, mgr)
    assert result.added == []
    assert result.removed == []


def test_reconcile_unsubscribe_resurfaces_local_rule(mgr: NftManager, db_session):  # type: ignore[no-untyped-def]
    """End-to-end unsubscribe: removing a subscription /24 restores the local /32.

    After the first reconcile the kernel set contains only the /24 (the /32
    was absorbed by collapse).  Deleting the subscription rule and reconciling
    again must add the /32 back and remove the /24.
    """
    sub_rule = _make_rule("203.0.113.0", 24, source="subscription:1")
    local_rule = _make_rule("203.0.113.4", 32, source="local")
    db_session.add(sub_rule)
    db_session.add(local_rule)
    db_session.flush()
    reconcile_blocklist(db_session, mgr)
    assert mgr.list_blocklist() == [("203.0.113.0", 24)]

    db_session.delete(sub_rule)
    db_session.flush()
    result = reconcile_blocklist(db_session, mgr)
    assert result.added == [("203.0.113.4", 32)]
    assert result.removed == [("203.0.113.0", 24)]
    assert mgr.list_blocklist() == [("203.0.113.4", 32)]
