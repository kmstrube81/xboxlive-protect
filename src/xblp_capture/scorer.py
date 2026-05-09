"""Per-peer rolling-window scoring engine (see DESIGN.md §5.2).

Design notes:
- PeerScorer consumes PacketEvent objects from any source (live capture, pcap
  replay, test fixtures) and has no knowledge of how they were produced.
- ``recent_qualifications`` is a bounded deque[bool] of length
  min_consecutive_windows.  Each tick appends whether the peer's current pps
  met the threshold; old entries auto-evict.  This caps the score ceiling so
  a peer that has hosted all session doesn't dominate a freshly-joined one.
- ``tick()`` is idempotent in the sense that no *new* DetectedHost entries are
  returned for the same or non-advancing ``now``.  Any hosts detected on the
  first call have ``already_reported=True``; subsequent calls return [].
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from xblp_capture.events import PacketEvent
from xblp_common.profiles import Profile

# ── Public result types ───────────────────────────────────────────────────────


@dataclass
class PeerScore:
    """Snapshot of a single peer's current scoring state."""

    ip: str
    packets_per_second: float
    total_packets: int
    last_seen: float
    qualified_recent_windows: int  # sum of recent_qualifications deque; bounded
    score: float  # pps * qualified_recent_windows


@dataclass
class DetectedHost:
    """A peer that crossed the detection threshold for the first time this tick."""

    ip: str
    score: float
    duration_seconds: float  # now - qualified_since at detection time
    first_seen: float  # timestamp of first observed packet from this peer
    last_seen: float  # timestamp of most recent observed packet


# ── Internal state ────────────────────────────────────────────────────────────


@dataclass
class _PeerState:
    packets: deque[tuple[float, int]]  # (timestamp, length), arrival order
    recent_qualifications: deque[bool]  # maxlen=min_consecutive_windows
    first_seen: float  # timestamp of first observed packet
    last_seen: float  # timestamp of most recent observed packet
    qualified_since: float | None = None  # set on first True; cleared when deque all-False
    already_reported: bool = False


# ── Scorer ────────────────────────────────────────────────────────────────────


class PeerScorer:
    """Maintains rolling-window peer statistics for one game profile.

    Constructor args:
        xbox_ip: Only packets where this IP is src or dst are analysed.
        profile: Detection thresholds from the active game profile.
        allowlist_check: Optional callable; given a peer IP, returns True if it
            is on the Xbox Live allowlist and must be excluded from scoring.
            Defaults to None (no filtering).  The live daemon passes a function
            that queries the nftables allowlist set; tests pass a simple lambda.
    """

    def __init__(
        self,
        xbox_ip: str,
        profile: Profile,
        allowlist_check: Callable[[str], bool] | None = None,
    ) -> None:
        self._xbox_ip = xbox_ip
        self._profile = profile
        self._allowlist_check = allowlist_check
        self._peers: dict[str, _PeerState] = {}
        self._last_tick_at: float | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def observe(self, event: PacketEvent) -> None:
        """Record a packet event.  Silently ignores irrelevant packets."""
        if event.src_ip != self._xbox_ip and event.dst_ip != self._xbox_ip:
            return
        if event.transport != self._profile.detection.transport:
            return
        if not self._port_matches(event.src_port) and not self._port_matches(event.dst_port):
            return

        peer_ip = event.dst_ip if event.src_ip == self._xbox_ip else event.src_ip

        if self._allowlist_check is not None and self._allowlist_check(peer_ip):
            return

        if peer_ip not in self._peers:
            self._peers[peer_ip] = _PeerState(
                packets=deque(),
                recent_qualifications=deque(maxlen=self._profile.detection.min_consecutive_windows),
                first_seen=event.timestamp,
                last_seen=event.timestamp,
            )

        state = self._peers[peer_ip]
        state.packets.append((event.timestamp, event.length))
        if event.timestamp > state.last_seen:
            state.last_seen = event.timestamp

    def peer_table(self, now: float) -> list[PeerScore]:
        """Return current peer scores at ``now``, sorted by score descending.

        Ties in score are broken by total_packets (more packets ranks higher).
        """
        window_start = now - self._profile.detection.window_seconds
        scores: list[PeerScore] = []

        for ip, state in self._peers.items():
            count_in_window = sum(1 for ts, _ in state.packets if ts >= window_start)
            pps = count_in_window / self._profile.detection.window_seconds
            qualified_count = sum(state.recent_qualifications)
            scores.append(
                PeerScore(
                    ip=ip,
                    packets_per_second=pps,
                    total_packets=len(state.packets),
                    last_seen=state.last_seen,
                    qualified_recent_windows=qualified_count,
                    score=pps * qualified_count,
                )
            )

        scores.sort(key=lambda s: (s.score, s.total_packets), reverse=True)
        return scores

    def tick(self, now: float) -> list[DetectedHost]:
        """Update rolling qualification windows and return newly-detected hosts.

        Call once per second (or at any consistent interval).  Returns a list of
        peers that crossed the detection threshold for the *first* time this
        call.

        Idempotent: calling again with the same or non-advancing ``now`` returns
        [] because any hosts detected on the first call have already_reported=True.
        """
        if self._last_tick_at is not None and now <= self._last_tick_at:
            return []
        self._last_tick_at = now

        window_start = now - self._profile.detection.window_seconds
        detected: list[DetectedHost] = []

        for ip, state in self._peers.items():
            count_in_window = sum(1 for ts, _ in state.packets if ts >= window_start)
            pps = count_in_window / self._profile.detection.window_seconds
            qualifies = pps >= self._profile.detection.min_pps

            state.recent_qualifications.append(qualifies)

            if qualifies and state.qualified_since is None:
                state.qualified_since = now
            elif sum(state.recent_qualifications) == 0:
                state.qualified_since = None

            qualified_count = sum(state.recent_qualifications)

            if (
                qualified_count == self._profile.detection.min_consecutive_windows
                and not state.already_reported
            ):
                state.already_reported = True
                assert state.qualified_since is not None, (
                    "qualified_since must be set when emitting DetectedHost; "
                    "deque all-True implies at least one qualifying tick"
                )
                detected.append(
                    DetectedHost(
                        ip=ip,
                        score=pps * qualified_count,
                        duration_seconds=now - state.qualified_since,
                        first_seen=state.first_seen,
                        last_seen=state.last_seen,
                    )
                )

        return detected

    def prune(self, now: float, retention_seconds: int = 300) -> None:
        """Remove state for peers not seen within ``retention_seconds``.

        Also trims the packet deque for active peers to the current window so
        memory stays bounded even for long-lived high-rate peers.
        """
        cutoff = now - retention_seconds
        stale = [ip for ip, state in self._peers.items() if state.last_seen < cutoff]
        for ip in stale:
            del self._peers[ip]

        window_start = now - self._profile.detection.window_seconds
        for state in self._peers.values():
            while state.packets and state.packets[0][0] < window_start:
                state.packets.popleft()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _port_matches(self, port: int) -> bool:
        return any(r.min <= port <= r.max for r in self._profile.detection.port_ranges)
