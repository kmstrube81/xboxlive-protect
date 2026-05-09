"""Unit tests for the persistence layer (detected_hosts table)."""

import pytest
from sqlalchemy.orm import Session

from xblp_capture.persistence import record_detected_host
from xblp_capture.scorer import DetectedHost as ScoredHost
from xblp_common.models import DetectedHost


def _host(
    ip: str = "203.0.113.1",
    score: float = 90.0,
    duration_seconds: float = 30.0,
    first_seen: float = 1000.0,
    last_seen: float = 1030.0,
) -> ScoredHost:
    return ScoredHost(
        ip=ip,
        score=score,
        duration_seconds=duration_seconds,
        first_seen=first_seen,
        last_seen=last_seen,
    )


@pytest.mark.unit
def test_record_detected_host_inserts_row(db_session: Session) -> None:
    record_detected_host(db_session, _host(), profile_id="mw2-x360")

    rows = db_session.query(DetectedHost).all()
    assert len(rows) == 1
    assert rows[0].ip_address == "203.0.113.1"
    assert rows[0].score == pytest.approx(90.0)
    assert rows[0].duration_seconds == 30


@pytest.mark.unit
def test_record_detected_host_profile_id_recorded(db_session: Session) -> None:
    record_detected_host(db_session, _host(ip="203.0.113.99"), profile_id="halo3-x360")

    row = db_session.query(DetectedHost).first()
    assert row is not None
    assert row.profile_id == "halo3-x360"


@pytest.mark.unit
def test_record_detected_host_was_blocked_defaults_false(db_session: Session) -> None:
    record_detected_host(db_session, _host(ip="203.0.113.2"), profile_id="mw2-x360")

    row = db_session.query(DetectedHost).first()
    assert row is not None
    assert row.was_blocked is False


@pytest.mark.unit
def test_record_detected_host_multiple_rows_same_ip(db_session: Session) -> None:
    # Each call inserts a new row — the history is intentional (no dedup)
    for i in range(3):
        record_detected_host(
            db_session,
            _host(ip="203.0.113.1", last_seen=1000.0 + i * 60),
            profile_id="mw2-x360",
        )

    rows = db_session.query(DetectedHost).all()
    assert len(rows) == 3
