"""Persist scorer output to the detected_hosts and peer_snapshots tables."""

from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from xblp_capture.scorer import DetectedHost as ScoredHost, PeerSnapshotStats
from xblp_common.models import DetectedHost, PeerSnapshot, RuntimeState

log = structlog.get_logger(__name__)

_SNAPSHOT_RETENTION_SECONDS = 300  # 5-minute rolling window


def record_detected_host(session: Session, host: ScoredHost, profile_id: str) -> None:
    """Insert one detected_hosts row from the scorer's output.

    Geolocation (asn, country_code) is left NULL; that is an enrichment step
    added later.  was_blocked defaults to False — whether the IP is in the
    blocklist is not checked here.
    """
    row = DetectedHost(
        detected_at=datetime.fromtimestamp(host.last_seen, tz=UTC).replace(tzinfo=None),
        ip_address=host.ip,
        profile_id=profile_id,
        score=host.score,
        duration_seconds=int(host.duration_seconds),
        asn=None,
        country_code=None,
        was_blocked=False,
    )
    session.add(row)
    session.commit()
    log.info("detected host recorded", ip=host.ip, profile_id=profile_id, score=host.score)


def flush_peer_snapshots(
    session: Session,
    stats: list[PeerSnapshotStats],
    now_epoch: float,
) -> None:
    """Write one batch of PeerSnapshot rows and prune rows older than 5 minutes.

    Called once per second from the capture daemon main loop.  Each call
    writes len(stats) new rows (one per active peer) and deletes rows whose
    captured_at is older than _SNAPSHOT_RETENTION_SECONDS.

    ``now_epoch`` is the Unix epoch timestamp for the batch (time.time()).
    All rows in the batch share the same captured_at value so the API daemon
    can retrieve the latest batch with a single MAX(captured_at) query.
    """
    batch_ts = datetime.fromtimestamp(now_epoch, tz=UTC).replace(tzinfo=None)
    cutoff_ts = datetime.fromtimestamp(now_epoch - _SNAPSHOT_RETENTION_SECONDS, tz=UTC).replace(
        tzinfo=None
    )

    for stat in stats:
        session.add(
            PeerSnapshot(
                captured_at=batch_ts,
                peer_ip=stat.ip,
                pps=stat.pps,
                pps_5s=stat.pps_5s,
                score=stat.score,
                flagged=stat.flagged,
                bytes_in=stat.bytes_in,
                bytes_out=stat.bytes_out,
                first_seen_at=datetime.fromtimestamp(stat.first_seen, tz=UTC).replace(tzinfo=None),
                last_seen_at=datetime.fromtimestamp(stat.last_seen, tz=UTC).replace(tzinfo=None),
            )
        )

    # Prune rows older than the retention window.
    session.query(PeerSnapshot).filter(PeerSnapshot.captured_at < cutoff_ts).delete(
        synchronize_session=False
    )
    session.commit()


def write_active_profile(session: Session, profile_id: str) -> None:
    """Upsert the active_profile key in runtime_state.

    Called by the capture daemon at startup and whenever the profile changes.
    The API daemon reads this key to populate the active_profile field in
    GET /status.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = session.get(RuntimeState, "active_profile")
    if existing is not None:
        existing.value = profile_id
        existing.updated_at = now
    else:
        session.add(RuntimeState(key="active_profile", value=profile_id, updated_at=now))
    session.commit()
    log.info("runtime_state active_profile written", profile_id=profile_id)
