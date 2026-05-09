"""Unit tests for PeerScorer — the core detection engine."""

from datetime import date
from pathlib import Path

import pytest

from xblp_capture.events import PacketEvent
from xblp_capture.scorer import PeerScorer
from xblp_common.profiles import DetectionConfig, PortRange, Profile, load_profile

# ── Constants ─────────────────────────────────────────────────────────────────

XBOX = "192.168.1.100"
PEER_A = "203.0.113.10"
PEER_B = "203.0.113.20"
EXTERNAL = "1.2.3.4"  # unrelated IP (neither Xbox nor observed peer)
BASE = 1_000.0  # arbitrary epoch anchor

PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _profile(
    min_pps: float = 30.0,
    window_seconds: int = 1,
    min_consecutive_windows: int = 3,
    transport: str = "udp",
) -> Profile:
    """Construct a minimal synthetic Profile for unit testing.

    window_seconds=1 keeps timestamp arithmetic readable; real profiles use 10.
    """
    return Profile(
        id="test",
        name="Test Profile",
        console="xbox-360",
        confidence="tested",
        maintainer="test",
        last_validated=date(2026, 1, 1),
        detection=DetectionConfig(
            transport=transport,
            port_ranges=[PortRange(min=1000, max=65535)],
            min_pps=min_pps,
            window_seconds=window_seconds,
            min_consecutive_windows=min_consecutive_windows,
        ),
        exclude_ranges=[],
        payload_signatures=[],
    )


def _pkt(
    src_ip: str = XBOX,
    dst_ip: str = PEER_A,
    timestamp: float = BASE,
    src_port: int = 3074,
    dst_port: int = 3074,
    transport: str = "udp",
    length: int = 100,
) -> PacketEvent:
    return PacketEvent(
        timestamp=timestamp,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        transport=transport,
        length=length,
    )


def _pump(
    scorer: PeerScorer,
    src_ip: str,
    dst_ip: str,
    count: int,
    t_start: float,
    t_end: float,
) -> None:
    """Observe ``count`` evenly-spaced packets between t_start and t_end (exclusive)."""
    step = (t_end - t_start) / count
    for i in range(count):
        scorer.observe(_pkt(src_ip=src_ip, dst_ip=dst_ip, timestamp=t_start + i * step))


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_observe_irrelevant_packets_ignored() -> None:
    scorer = PeerScorer(xbox_ip=XBOX, profile=_profile())

    # Packet not involving Xbox at all
    scorer.observe(_pkt(src_ip=PEER_A, dst_ip=EXTERNAL))
    # Wrong transport (profile expects UDP)
    scorer.observe(_pkt(src_ip=XBOX, dst_ip=PEER_A, transport="tcp"))
    # Both ports outside profile range (1000-65535); port 80 is below range
    scorer.observe(_pkt(src_ip=XBOX, dst_ip=PEER_A, src_port=80, dst_port=80))

    assert scorer.peer_table(BASE) == []


@pytest.mark.unit
def test_observe_relevant_packet_recorded() -> None:
    scorer = PeerScorer(xbox_ip=XBOX, profile=_profile())
    scorer.observe(_pkt(src_ip=XBOX, dst_ip=PEER_A, timestamp=BASE))

    table = scorer.peer_table(BASE)
    assert len(table) == 1
    assert table[0].ip == PEER_A


@pytest.mark.unit
def test_packets_per_second_calculation() -> None:
    # window_seconds=1, 30 packets in [BASE, BASE+1) → pps should be 30.0
    scorer = PeerScorer(
        xbox_ip=XBOX,
        profile=_profile(min_pps=1, window_seconds=1, min_consecutive_windows=1),
    )
    _pump(scorer, XBOX, PEER_A, 30, BASE, BASE + 1.0)

    table = scorer.peer_table(now=BASE + 1.0)
    assert len(table) == 1
    assert table[0].packets_per_second == pytest.approx(30.0, abs=0.1)


@pytest.mark.unit
def test_score_ranking() -> None:
    # Peer A (50 pps) should rank above Peer B (20 pps) after one qualifying tick
    p = _profile(min_pps=10, window_seconds=1, min_consecutive_windows=1)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)
    _pump(scorer, XBOX, PEER_A, 50, BASE, BASE + 1.0)
    _pump(scorer, XBOX, PEER_B, 20, BASE, BASE + 1.0)

    scorer.tick(now=BASE + 1.0)  # updates recent_qualifications

    table = scorer.peer_table(now=BASE + 1.0)
    assert len(table) == 2
    assert table[0].ip == PEER_A
    assert table[1].ip == PEER_B
    assert table[0].score > table[1].score


@pytest.mark.unit
def test_window_expiration() -> None:
    # Packets at BASE should not count toward pps at BASE+2 (window_seconds=1)
    scorer = PeerScorer(xbox_ip=XBOX, profile=_profile(window_seconds=1))
    for _ in range(30):
        scorer.observe(_pkt(timestamp=BASE))

    assert scorer.peer_table(now=BASE)[0].packets_per_second == pytest.approx(30.0)

    # At BASE+2, window is [BASE+1, BASE+2] — all packets (at BASE) are expired
    assert scorer.peer_table(now=BASE + 2.0)[0].packets_per_second == pytest.approx(0.0)


@pytest.mark.unit
def test_min_pps_threshold() -> None:
    # 5 packets in 1 second = 5 pps < min_pps 30 → peer should not qualify
    scorer = PeerScorer(
        xbox_ip=XBOX,
        profile=_profile(min_pps=30, window_seconds=1, min_consecutive_windows=1),
    )
    _pump(scorer, XBOX, PEER_A, 5, BASE, BASE + 1.0)

    detected = scorer.tick(now=BASE + 1.0)
    assert detected == []
    assert scorer.peer_table(now=BASE + 1.0)[0].qualified_recent_windows == 0


@pytest.mark.unit
def test_min_consecutive_windows() -> None:
    # min_consecutive_windows=3: first two qualifying ticks don't trigger detection;
    # the third does.  Each window is 1 second wide; we pump fresh packets each window
    # so the pps stays at 30 throughout.
    p = _profile(min_pps=30, window_seconds=1, min_consecutive_windows=3)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)

    _pump(scorer, XBOX, PEER_A, 30, BASE, BASE + 1.0)
    assert scorer.tick(now=BASE + 1.0) == []  # 1 qualifying window

    _pump(scorer, XBOX, PEER_A, 30, BASE + 1.0, BASE + 2.0)
    assert scorer.tick(now=BASE + 2.0) == []  # 2 qualifying windows

    _pump(scorer, XBOX, PEER_A, 30, BASE + 2.0, BASE + 3.0)
    detected = scorer.tick(now=BASE + 3.0)  # 3 → detection fires
    assert len(detected) == 1
    assert detected[0].ip == PEER_A


@pytest.mark.unit
def test_already_reported_not_re_emitted() -> None:
    # Once a DetectedHost is emitted, subsequent ticks never re-emit the same IP
    p = _profile(min_pps=30, window_seconds=1, min_consecutive_windows=1)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)

    _pump(scorer, XBOX, PEER_A, 30, BASE, BASE + 1.0)
    first = scorer.tick(now=BASE + 1.0)
    assert len(first) == 1

    _pump(scorer, XBOX, PEER_A, 30, BASE + 1.0, BASE + 2.0)
    second = scorer.tick(now=BASE + 2.0)
    assert second == []


@pytest.mark.unit
def test_allowlist_filters_peers() -> None:
    allowlisted = {PEER_A}
    scorer = PeerScorer(
        xbox_ip=XBOX,
        profile=_profile(min_pps=1, window_seconds=1, min_consecutive_windows=1),
        allowlist_check=lambda ip: ip in allowlisted,
    )
    _pump(scorer, XBOX, PEER_A, 100, BASE, BASE + 1.0)

    # Allowlisted peer must never appear in the peer table or detections
    assert scorer.peer_table(now=BASE + 1.0) == []
    assert scorer.tick(now=BASE + 1.0) == []


@pytest.mark.unit
def test_prune_removes_stale_peers() -> None:
    scorer = PeerScorer(xbox_ip=XBOX, profile=_profile())
    scorer.observe(_pkt(timestamp=BASE))

    # Peer is visible before prune
    assert len(scorer.peer_table(now=BASE + 1.0)) == 1

    # After retention_seconds elapses, peer is removed
    scorer.prune(now=BASE + 11.0, retention_seconds=10)
    assert scorer.peer_table(now=BASE + 11.0) == []


@pytest.mark.unit
def test_tick_no_new_detections_on_repeat_now() -> None:
    # Calling tick() twice with the same `now` returns [] on the second call.
    # Any host detected on the first call has already_reported=True, so the
    # second call has nothing new to report.  This is the intended semantic:
    # tick() returns *newly-detected* hosts; idempotency is implicit.
    p = _profile(min_pps=30, window_seconds=1, min_consecutive_windows=1)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)
    _pump(scorer, XBOX, PEER_A, 30, BASE, BASE + 1.0)

    first = scorer.tick(now=BASE + 1.0)
    second = scorer.tick(now=BASE + 1.0)  # same now

    assert len(first) == 1
    assert second == []


@pytest.mark.unit
def test_xbox_can_be_either_src_or_dst() -> None:
    # Packets where Xbox is dst (peer is src) must also be counted for that peer
    scorer = PeerScorer(xbox_ip=XBOX, profile=_profile())

    scorer.observe(_pkt(src_ip=XBOX, dst_ip=PEER_A, timestamp=BASE))
    scorer.observe(_pkt(src_ip=PEER_A, dst_ip=XBOX, timestamp=BASE + 0.1))

    table = scorer.peer_table(now=BASE + 0.1)
    assert len(table) == 1
    assert table[0].ip == PEER_A
    assert table[0].total_packets == 2


@pytest.mark.unit
def test_monitoring_only_profile() -> None:
    # monitoring-only has min_pps=999999; no amount of real traffic triggers detection
    monitoring = load_profile(PROFILES_DIR / "monitoring-only.yaml")
    scorer = PeerScorer(xbox_ip=XBOX, profile=monitoring)

    # Flood with 1000 packets; pps = 1000 / window_seconds(10) = 100 << 999999
    _pump(scorer, XBOX, PEER_A, 1000, BASE, BASE + 1.0)

    assert scorer.tick(now=BASE + 1.0) == []


@pytest.mark.unit
def test_qualified_count_bounded() -> None:
    # qualified_recent_windows is capped at min_consecutive_windows regardless of
    # how many qualifying ticks occur.  A peer hosting for an hour should not
    # dominate a peer that just joined.
    p = _profile(min_pps=30, window_seconds=1, min_consecutive_windows=3)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)

    for i in range(10):
        _pump(scorer, XBOX, PEER_A, 30, BASE + i, BASE + i + 1.0)
        scorer.tick(now=BASE + i + 1.0)

    table = scorer.peer_table(now=BASE + 10.0)
    assert len(table) == 1
    assert table[0].qualified_recent_windows == 3  # capped, not 10


@pytest.mark.unit
def test_peer_drops_out_of_host_status() -> None:
    # After qualifying for 3 ticks (detection fires), if the peer stops sending
    # packets the deque fills with False entries and qualified_count decays to 0.
    p = _profile(min_pps=30, window_seconds=1, min_consecutive_windows=3)
    scorer = PeerScorer(xbox_ip=XBOX, profile=p)

    # Three qualifying ticks → detection
    for i in range(3):
        _pump(scorer, XBOX, PEER_A, 30, BASE + i, BASE + i + 1.0)
        scorer.tick(now=BASE + i + 1.0)

    # Three silent ticks: deque rolls [T,T,T] → [T,T,F] → [T,F,F] → [F,F,F]
    for i in range(3, 6):
        scorer.tick(now=BASE + i + 1.0)

    table = scorer.peer_table(now=BASE + 7.0)
    assert table[0].qualified_recent_windows == 0
