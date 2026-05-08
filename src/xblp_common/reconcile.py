"""Blocklist reconciler: the DB is the source of truth; the nft set is a projection.

Call ``reconcile_blocklist`` after any change to the ``rules`` table so the
kernel set stays in sync with persisted state.

Intended call sites (Stage 4+):

- After adding a local rule
- After deleting a local rule
- After subscribing to a list
- After unsubscribing from a list
- After a subscription refresh applies updates to the rules table
- After promoting a subscription rule to local

``reconcile_blocklist`` is the only function that should call
``nft.add_to_blocklist`` or ``nft.remove_from_blocklist``.  Direct calls to
those from elsewhere in the codebase will fight with the diff calculation here
and produce nft state thrash.  The nft manager exposes them as public methods
only because reconcile.py needs them — treat them as effectively private to
this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from xblp_common.models import Rule
from xblp_common.nft import _collapse_entries

log = structlog.get_logger(__name__)


class _BlocklistManager(Protocol):
    def list_blocklist(self) -> list[tuple[str, int]]: ...
    def add_to_blocklist(self, ip: str, cidr: int = 32) -> None: ...
    def remove_from_blocklist(self, ip: str, cidr: int = 32) -> None: ...


@dataclass(frozen=True)
class ReconcileResult:
    added: list[tuple[str, int]]
    removed: list[tuple[str, int]]
    duration_ms: float


def reconcile_blocklist(session: Session, nft: _BlocklistManager) -> ReconcileResult:
    """Diff DB rules against the live nft blocklist and apply the delta.

    Reads nft state first: if that call raises, the function propagates the
    error without making any partial writes.  Overlapping DB entries are
    collapsed via :func:`~xblp_common.nft._collapse_entries` before diffing,
    matching the same normalisation applied to allowlist writes.
    """
    t0 = time.monotonic()
    current: set[tuple[str, int]] = set(nft.list_blocklist())

    db_rules: list[Rule] = session.query(Rule).all()
    desired: set[tuple[str, int]] = set(
        _collapse_entries([(r.ip_address, r.cidr_prefix) for r in db_rules])
    )

    to_add: list[tuple[str, int]] = sorted(desired - current)
    to_remove: list[tuple[str, int]] = sorted(current - desired)

    for ip, cidr in to_add:
        nft.add_to_blocklist(ip, cidr)
    for ip, cidr in to_remove:
        nft.remove_from_blocklist(ip, cidr)

    duration_ms = (time.monotonic() - t0) * 1000
    result = ReconcileResult(added=to_add, removed=to_remove, duration_ms=duration_ms)
    log.info(
        "blocklist reconciled",
        added=len(to_add),
        removed=len(to_remove),
        duration_ms=round(duration_ms, 1),
    )
    return result
