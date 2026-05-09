"""Live packet capture using scapy (Linux + root only, see DESIGN.md §4.2).

This module is intentionally thin: it translates raw scapy packets into
PacketEvent objects and nothing more.  All analysis happens in scorer.py.

Requires:
    - Linux (raw socket support)
    - Root privileges (or CAP_NET_RAW)
    - scapy installed (runtime dependency, in pyproject.toml)
"""

from __future__ import annotations

import queue
from collections.abc import Iterator
from typing import Any

import structlog
from scapy.layers.inet import IP, TCP, UDP
from scapy.sendrecv import AsyncSniffer

from xblp_capture.events import PacketEvent

log = structlog.get_logger(__name__)


def _parse_packet(pkt: Any) -> PacketEvent | None:
    """Extract a PacketEvent from a scapy packet, or None if not TCP/UDP over IP."""
    if not pkt.haslayer(IP):
        return None

    ip = pkt[IP]
    ts = float(pkt.time)
    src_ip = str(ip.src)
    dst_ip = str(ip.dst)
    length = len(pkt)

    if pkt.haslayer(UDP):
        layer = pkt[UDP]
        return PacketEvent(
            timestamp=ts,
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=int(layer.sport),
            dst_port=int(layer.dport),
            transport="udp",
            length=length,
        )
    if pkt.haslayer(TCP):
        layer = pkt[TCP]
        return PacketEvent(
            timestamp=ts,
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=int(layer.sport),
            dst_port=int(layer.dport),
            transport="tcp",
            length=length,
        )
    return None


def live_capture(interface: str, bpf_filter: str | None = None) -> Iterator[PacketEvent]:
    """Yield PacketEvent objects from a live interface capture.

    Runs AsyncSniffer in a background thread and feeds events through a queue.
    Breaking out of the generator (or letting it be garbage-collected) stops
    the sniffer cleanly via the finally block.

    Args:
        interface: Network interface name (e.g. "eth0", "br0").
        bpf_filter: Optional BPF filter string applied at the kernel level
            (e.g. "host 192.168.1.50").  Reduces Python-layer packet volume.
    """
    packet_queue: queue.Queue[PacketEvent] = queue.Queue()

    def _on_packet(pkt: Any) -> None:
        event = _parse_packet(pkt)
        if event is not None:
            packet_queue.put(event)

    kwargs: dict[str, Any] = {
        "iface": interface,
        "prn": _on_packet,
        "store": False,
    }
    if bpf_filter:
        kwargs["filter"] = bpf_filter

    sniffer: Any = AsyncSniffer(**kwargs)
    sniffer.start()
    log.info("capture started", interface=interface, bpf_filter=bpf_filter)

    try:
        while True:
            try:
                yield packet_queue.get(timeout=0.1)
            except queue.Empty:
                continue
    finally:
        sniffer.stop()
        log.info("capture stopped", interface=interface)
