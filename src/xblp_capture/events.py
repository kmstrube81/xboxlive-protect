"""PacketEvent — boundary type between raw capture and analysis (see DESIGN.md §4.2).

This module is intentionally pure: no imports from scapy, sockets, or any
networking library. PacketEvent is the seam that makes the scorer 100%
unit-testable without raw sockets.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PacketEvent:
    """One observed IP packet extracted from a live or replayed capture."""

    timestamp: float  # Unix epoch, seconds with sub-second precision (from kernel)
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    transport: Literal["tcp", "udp"]
    length: int  # total packet length in bytes; populated for free from scapy
