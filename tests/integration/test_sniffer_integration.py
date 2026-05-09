"""Integration test for live_capture (Linux + root only).

Sends a real UDP packet on the loopback interface via scapy and verifies that
live_capture yields a correctly-populated PacketEvent.

Run with:
    sudo pytest -m integration -v
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.linux]

_TEST_PORT = 19991  # arbitrary high port; unlikely to clash with any service


def _skip_unless_linux_root() -> None:
    if sys.platform != "linux":
        pytest.skip("sniffer integration tests require Linux")
    if os.geteuid() != 0:
        pytest.skip("sniffer integration tests require root (CAP_NET_RAW)")


@pytest.fixture(autouse=True)
def require_linux_root() -> None:
    _skip_unless_linux_root()


def test_live_capture_yields_packet_events() -> None:
    """live_capture on loopback yields a PacketEvent with correct field types."""
    from scapy.layers.inet import IP, UDP  # type: ignore[import-untyped]
    from scapy.sendrecv import send  # type: ignore[import-untyped]

    from xblp_capture.events import PacketEvent
    from xblp_capture.sniffer import live_capture

    captured: queue.Queue[PacketEvent] = queue.Queue()

    def _run_capture() -> None:
        bpf = f"udp port {_TEST_PORT}"
        for event in live_capture("lo", bpf_filter=bpf):
            captured.put(event)
            break  # stop after first event; generator finally stops the sniffer

    capture_thread = threading.Thread(target=_run_capture, daemon=True)
    capture_thread.start()

    # Give AsyncSniffer a moment to bind before sending
    time.sleep(0.3)

    send(
        IP(dst="127.0.0.1") / UDP(sport=_TEST_PORT, dport=_TEST_PORT) / b"xblptest",
        verbose=False,
    )

    try:
        event = captured.get(timeout=5.0)
    except queue.Empty:
        pytest.fail("No packet captured within 5 seconds")

    capture_thread.join(timeout=2.0)

    assert event.transport == "udp"
    assert event.src_port == _TEST_PORT or event.dst_port == _TEST_PORT
    assert isinstance(event.timestamp, float)
    assert event.timestamp > 0.0
    assert isinstance(event.length, int)
    assert event.length > 0
    assert event.src_ip != ""
    assert event.dst_ip != ""


def test_live_capture_skips_non_ip_packets() -> None:
    """_parse_packet returns None for non-IP frames; live_capture never yields them."""
    from xblp_capture.sniffer import _parse_packet

    # Simulate a raw object with no IP layer (minimal duck-typing)
    class _FakePkt:
        def haslayer(self, cls: object) -> bool:
            return False

        def __len__(self) -> int:
            return 14  # Ethernet header only

    assert _parse_packet(_FakePkt()) is None
