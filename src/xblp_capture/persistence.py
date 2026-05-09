"""Persist scorer output to the detected_hosts table (see DESIGN.md §7.4)."""

from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from xblp_capture.scorer import DetectedHost as ScoredHost
from xblp_common.models import DetectedHost

log = structlog.get_logger(__name__)


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
