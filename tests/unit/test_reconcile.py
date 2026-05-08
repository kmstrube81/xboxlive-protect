"""Unit tests for xblp_common.reconcile — all nft calls use in-process stubs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from xblp_common.models import Rule
from xblp_common.nft import NftError
from xblp_common.reconcile import reconcile_blocklist

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_rule(ip: str, cidr: int = 32, source: str = "local") -> Rule:
    now = _now()
    return Rule(ip_address=ip, cidr_prefix=cidr, source=source, created_at=now, updated_at=now)


class _MockNft:
    """In-process stub that mutates its own state so successive list calls reflect prior ops."""

    def __init__(self, current: list[tuple[str, int]] | None = None) -> None:
        self._current: list[tuple[str, int]] = list(current or [])
        self.added: list[tuple[str, int]] = []
        self.removed: list[tuple[str, int]] = []

    def list_blocklist(self) -> list[tuple[str, int]]:
        return list(self._current)

    def add_to_blocklist(self, ip: str, cidr: int = 32) -> None:
        self.added.append((ip, cidr))
        self._current.append((ip, cidr))

    def remove_from_blocklist(self, ip: str, cidr: int = 32) -> None:
        self.removed.append((ip, cidr))
        self._current = [(i, c) for i, c in self._current if (i, c) != (ip, cidr)]


class _FailingNft:
    """Stub whose list_blocklist always raises, tracking whether add/remove are called."""

    def __init__(self) -> None:
        self.add_calls: int = 0
        self.remove_calls: int = 0

    def list_blocklist(self) -> list[tuple[str, int]]:
        raise NftError("ruleset not present")

    def add_to_blocklist(self, ip: str, cidr: int = 32) -> None:
        self.add_calls += 1

    def remove_from_blocklist(self, ip: str, cidr: int = 32) -> None:
        self.remove_calls += 1


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_empty_db_empty_nft_no_changes(db_session: Session) -> None:
    result = reconcile_blocklist(db_session, _MockNft())
    assert result.added == []
    assert result.removed == []


def test_db_entries_added_when_nft_empty(db_session: Session) -> None:
    db_session.add(_make_rule("192.0.2.1"))
    db_session.add(_make_rule("192.0.2.2"))
    db_session.flush()
    result = reconcile_blocklist(db_session, _MockNft())
    assert set(result.added) == {("192.0.2.1", 32), ("192.0.2.2", 32)}
    assert result.removed == []


def test_nft_entries_removed_when_db_empty(db_session: Session) -> None:
    nft = _MockNft(current=[("1.2.3.4", 32)])
    result = reconcile_blocklist(db_session, nft)
    assert result.added == []
    assert result.removed == [("1.2.3.4", 32)]


def test_matching_state_is_noop(db_session: Session) -> None:
    db_session.add(_make_rule("10.0.0.1"))
    db_session.flush()
    result = reconcile_blocklist(db_session, _MockNft(current=[("10.0.0.1", 32)]))
    assert result.added == []
    assert result.removed == []


def test_overlapping_db_entries_collapse_before_diff(db_session: Session) -> None:
    """A /32 inside a /24 in the DB collapses to the /24, matching nft — no writes needed."""
    db_session.add(_make_rule("203.0.113.0", 24, source="subscription:1"))
    db_session.add(_make_rule("203.0.113.4", 32, source="local"))
    db_session.flush()
    nft = _MockNft(current=[("203.0.113.0", 24)])
    result = reconcile_blocklist(db_session, nft)
    assert result.added == []
    assert result.removed == []


def test_idempotency(db_session: Session) -> None:
    db_session.add(_make_rule("172.16.0.1"))
    db_session.flush()
    nft = _MockNft()
    reconcile_blocklist(db_session, nft)
    result = reconcile_blocklist(db_session, nft)
    assert result.added == []
    assert result.removed == []


def test_unsubscribe_resurfaces_local_rule(db_session: Session) -> None:
    """Deleting a subscription /24 that absorbed a local /32 restores the /32 in nft.

    While both rules are present, the /32 collapses into the /24 and nft sees
    only the /24.  After the subscription rule is deleted, the desired set
    becomes just the /32, so reconcile adds it back and drops the /24.
    """
    sub_rule = _make_rule("203.0.113.0", 24, source="subscription:1")
    local_rule = _make_rule("203.0.113.4", 32, source="local")
    db_session.add(sub_rule)
    db_session.add(local_rule)
    db_session.flush()

    nft = _MockNft()
    reconcile_blocklist(db_session, nft)
    assert nft._current == [("203.0.113.0", 24)]

    db_session.delete(sub_rule)
    db_session.flush()

    result = reconcile_blocklist(db_session, nft)
    assert result.added == [("203.0.113.4", 32)]
    assert result.removed == [("203.0.113.0", 24)]
    assert nft._current == [("203.0.113.4", 32)]


def test_result_reflects_applied_changes(db_session: Session) -> None:
    db_session.add(_make_rule("10.0.0.1"))
    db_session.flush()
    nft = _MockNft(current=[("10.0.0.2", 32)])
    result = reconcile_blocklist(db_session, nft)
    assert result.added == [("10.0.0.1", 32)]
    assert result.removed == [("10.0.0.2", 32)]


def test_duration_ms_nonnegative(db_session: Session) -> None:
    result = reconcile_blocklist(db_session, _MockNft())
    assert result.duration_ms >= 0.0


def test_list_blocklist_error_propagates_no_partial_writes(db_session: Session) -> None:
    db_session.add(_make_rule("192.0.2.1"))
    db_session.flush()
    nft = _FailingNft()
    with pytest.raises(NftError):
        reconcile_blocklist(db_session, nft)
    assert nft.add_calls == 0
    assert nft.remove_calls == 0
