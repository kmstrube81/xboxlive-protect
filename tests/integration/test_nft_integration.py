"""Integration tests for NftManager — require Linux, root, and the nft binary.

Run with:
    sudo pytest -m integration

These tests are automatically skipped on non-Linux platforms, when the nft
binary is absent, or when not running as root. Each test cleans up the
xblp_test table in a try/finally via the mgr fixture so a mid-test failure
never leaves kernel state behind.
"""

from __future__ import annotations

import contextlib
import platform
import shutil
from pathlib import Path

import pytest

from xblp_common.nft import NftError, NftManager

pytestmark = [pytest.mark.integration, pytest.mark.linux]

_TEST_TABLE = "xblp_test"


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


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mgr() -> NftManager:  # type: ignore[return]  # yield in try/finally
    m = NftManager(table=_TEST_TABLE)
    try:
        yield m
    finally:
        with contextlib.suppress(NftError):
            m.remove_ruleset()


# ── Ruleset lifecycle ─────────────────────────────────────────────────────────


def test_apply_and_verify_ruleset(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    assert mgr.verify_ruleset_present()


def test_apply_initial_ruleset_idempotent(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.apply_initial_ruleset()  # must not raise
    assert mgr.verify_ruleset_present()


def test_verify_ruleset_absent_before_apply(mgr: NftManager) -> None:
    assert not mgr.verify_ruleset_present()


def test_remove_ruleset(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.remove_ruleset()
    assert not mgr.verify_ruleset_present()


# ── Blocklist ─────────────────────────────────────────────────────────────────


def test_blocklist_empty_after_init(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    assert mgr.list_blocklist() == []


def test_blocklist_add_and_list(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.add_to_blocklist("192.0.2.1")
    assert ("192.0.2.1", 32) in mgr.list_blocklist()


def test_blocklist_remove(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.add_to_blocklist("192.0.2.2")
    mgr.remove_from_blocklist("192.0.2.2")
    assert ("192.0.2.2", 32) not in mgr.list_blocklist()


def test_blocklist_cidr_prefix(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.add_to_blocklist("203.0.113.0", 24)
    assert ("203.0.113.0", 24) in mgr.list_blocklist()


# ── Allowlist ─────────────────────────────────────────────────────────────────


def test_allowlist_add_and_list(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.add_to_allowlist("198.51.100.1")
    assert ("198.51.100.1", 32) in mgr.list_allowlist()


def test_allowlist_replace_atomic(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    entries = [("198.51.100.1", 32), ("198.51.100.0", 24)]
    mgr.replace_allowlist(entries)
    assert set(mgr.list_allowlist()) == set(entries)


def test_allowlist_replace_empty(mgr: NftManager) -> None:
    mgr.apply_initial_ruleset()
    mgr.replace_allowlist([("198.51.100.1", 32)])
    mgr.replace_allowlist([])
    assert mgr.list_allowlist() == []


# ── Error cases ───────────────────────────────────────────────────────────────


def test_nft_error_on_add_to_nonexistent_set(mgr: NftManager) -> None:
    """Adding to a set that doesn't exist raises NftError."""
    with pytest.raises(NftError):
        mgr.add_to_blocklist("192.0.2.3")
