"""Integration tests for Stage 5 bridge configuration.

Structural tests (TestBridgeStructure): verify br0 config, br_netfilter module,
sysctl, rollback infrastructure — no connected device required.

Live tests (TestBridgeLive): verify the bridge host can reach the internet and
that live_capture sees packets on br0 — no connected device required.

Traffic tests (TestBridgeTraffic, marked needs_device): verify nftables forward
chain counters increment on bridged traffic — requires a device connected to the
LAN (Xbox-facing) port.

Run all bridge tests:
    sudo .venv/bin/pytest tests/integration/test_bridge.py -v

Run traffic tests (after connecting a device to the LAN port):
    sudo .venv/bin/pytest tests/integration/test_bridge.py -v -m needs_device
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ── Platform guard ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True, scope="session")
def _require_linux() -> None:
    if platform.system() != "Linux":
        pytest.skip("Bridge integration tests require Linux")


# ── Shared helpers ────────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def _run_ok(cmd: list[str]) -> str:
    rc, stdout, stderr = _run(cmd)
    assert rc == 0, f"Command {cmd!r} failed (rc={rc}): {stderr.strip()}"
    return stdout


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def bridge_members() -> set[str]:
    """Interfaces that are bridge members of br0, read from sysfs."""
    br0 = Path("/sys/class/net/br0")
    if not br0.exists():
        pytest.skip("br0 is not configured — run bring-up-bridge.sh first")
    brif = br0 / "brif"
    if not brif.exists():
        pytest.skip("br0 exists but has no brif/ sysfs dir (not a bridge?)")
    return {p.name for p in brif.iterdir() if p.is_dir()}


@pytest.fixture(scope="module")
def br0_ip() -> str:
    """IPv4 address currently assigned to br0 via DHCP."""
    rc, stdout, _ = _run(["ip", "-4", "addr", "show", "dev", "br0"])
    if rc != 0:
        pytest.skip("br0 is not configured — run bring-up-bridge.sh first")
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", stdout)
    if not m:
        pytest.skip("br0 has no IPv4 address (DHCP may not have completed yet)")
    return m.group(1)


@pytest.fixture(scope="module")
def has_connected_device() -> bool:
    """True if a device is physically connected to the LAN (Xbox-facing) port.

    Checks carrier state on the LAN slave port — NOT br0's ARP table.
    br0's ARP table includes the WAN-side router (reached via the WAN slave)
    and any other LAN neighbors of the bridge host itself, none of which
    represent a device on the LAN side of the bridge.

    The LAN slave is discovered as "the br0 member that isn't carrying the
    default route" — same heuristic the bring-up script uses. Returns False
    on any error so tests using this fixture skip gracefully on misconfigured
    systems rather than blowing up.
    """
    # Discover WAN: the interface carrying the default route.
    rc, default_route, _ = _run(["ip", "route", "show", "default"])
    if rc != 0 or not default_route.strip():
        return False
    tokens = default_route.split()
    wan_iface: str | None = None
    for i, tok in enumerate(tokens):
        if tok == "dev" and i + 1 < len(tokens):
            wan_iface = tokens[i + 1]
            break
    if not wan_iface:
        return False

    # LAN is the bridge member that isn't WAN.
    brif = Path("/sys/class/net/br0/brif")
    if not brif.exists():
        return False
    try:
        members = [p.name for p in brif.iterdir() if p.is_dir()]
    except OSError:
        return False
    lan_candidates = [m for m in members if m != wan_iface]
    if len(lan_candidates) != 1:
        return False
    lan_iface = lan_candidates[0]

    # carrier == "1" means cable plugged in and physical link negotiated.
    try:
        carrier = Path(f"/sys/class/net/{lan_iface}/carrier").read_text().strip()
    except OSError:
        return False
    return carrier == "1"


# ── Structural tests (no connected device required) ───────────────────────────


class TestBridgeStructure:
    def test_br0_exists_and_is_up(self) -> None:
        rc, stdout, _ = _run(["ip", "link", "show", "br0"])
        assert rc == 0, "br0 interface does not exist"
        assert "UP" in stdout, f"br0 is not in UP state:\n{stdout}"

    def test_br0_has_two_members(self, bridge_members: set[str]) -> None:
        assert len(bridge_members) == 2, (
            f"Expected 2 bridge members, got {len(bridge_members)}: {bridge_members}"
        )

    def test_br0_members_are_physical_ethernet(self, bridge_members: set[str]) -> None:
        for iface in bridge_members:
            sys_net = Path(f"/sys/class/net/{iface}")
            assert sys_net.exists(), f"Interface {iface} missing from /sys/class/net/"
            assert (sys_net / "device").exists(), (
                f"{iface} has no /sys/class/net/{iface}/device link (appears virtual)"
            )
            iface_type = (sys_net / "type").read_text().strip()
            assert iface_type == "1", (
                f"{iface} is not Ethernet (type={iface_type!r})"
            )

    def test_br0_stp_disabled(self) -> None:
        stp = Path("/sys/class/net/br0/bridge/stp_state")
        if not stp.exists():
            pytest.skip("br0 bridge sysfs not available")
        assert stp.read_text().strip() == "0", (
            "br0 STP is not disabled — ports may be blocked for 30 s on each link-up. "
            "Check bridge_stp setting in /etc/network/interfaces.d/br0.conf"
        )

    def test_br0_forward_delay_zero(self) -> None:
        fd = Path("/sys/class/net/br0/bridge/forward_delay")
        if not fd.exists():
            pytest.skip("br0 bridge sysfs not available")
        assert fd.read_text().strip() == "0", (
            f"br0 forward_delay={fd.read_text().strip()!r} (expected 0). "
            "Ports will be blocked briefly after each link-up event."
        )

    def test_br_netfilter_loaded(self) -> None:
        modules = Path("/proc/modules").read_text()
        assert "br_netfilter" in modules, (
            "br_netfilter is not loaded. nftables cannot filter bridged traffic. "
            "Fix: sudo modprobe br_netfilter"
        )

    def test_sysctl_bridge_nf_call_iptables_is_1(self) -> None:
        val = Path("/proc/sys/net/bridge/bridge-nf-call-iptables").read_text().strip()
        assert val == "1", (
            f"net.bridge.bridge-nf-call-iptables={val!r} (expected '1'). "
            "nftables forward chain will not see bridged IPv4 traffic. "
            "Fix: sudo sysctl -w net.bridge.bridge-nf-call-iptables=1"
        )

    def test_sysctl_bridge_nf_call_ip6tables_is_0(self) -> None:
        val = Path("/proc/sys/net/bridge/bridge-nf-call-ip6tables").read_text().strip()
        assert val == "0", (
            f"net.bridge.bridge-nf-call-ip6tables={val!r} (expected '0'). "
            "xboxlive-protect is IPv4-only (DESIGN.md §1.2)."
        )

    def test_modules_load_config_installed(self) -> None:
        config = Path("/etc/modules-load.d/xblp-bridge.conf")
        assert config.exists(), (
            f"{config} not found. Was bring-up-bridge.sh run? "
            "br_netfilter will not load on next reboot."
        )
        assert "br_netfilter" in config.read_text()

    def test_sysctl_config_installed(self) -> None:
        config = Path("/etc/sysctl.d/99-xblp-bridge.conf")
        assert config.exists(), (
            f"{config} not found. Was bring-up-bridge.sh run? "
            "Bridge sysctls will not persist across reboots."
        )
        content = config.read_text()
        assert "net.bridge.bridge-nf-call-iptables=1" in content
        assert "net.bridge.bridge-nf-call-ip6tables=0" in content

    def test_rollback_service_installed_and_enabled(self) -> None:
        svc = Path("/etc/systemd/system/xblp-bridge-rollback.service")
        assert svc.exists(), (
            f"{svc} not found. Was bring-up-bridge.sh run?"
        )
        rc, stdout, _ = _run(["systemctl", "is-enabled", "xblp-bridge-rollback.service"])
        assert rc == 0 and "enabled" in stdout, (
            "xblp-bridge-rollback.service is not enabled. "
            "Fix: sudo systemctl enable xblp-bridge-rollback.service"
        )

    def test_rollback_script_installed(self) -> None:
        script = Path("/usr/local/lib/xboxlive-protect/rollback-bridge.sh")
        assert script.exists(), f"{script} not found. Was bring-up-bridge.sh run?"
        assert script.stat().st_mode & 0o111, f"{script} is not executable."

    def test_backup_exists(self) -> None:
        backup = Path("/etc/network/interfaces.xblp-backup/interfaces")
        assert backup.exists(), (
            f"Network config backup not found at {backup}. "
            "Was bring-up-bridge.sh run? The rollback service has no data to restore."
        )

    def test_sentinel_not_present(self) -> None:
        sentinel = Path("/etc/xboxlive-protect/.bridge-pending")
        assert not sentinel.exists(), (
            f"bridge-pending sentinel still present at {sentinel}. "
            "Run: sudo deploy/network/confirm-bridge.sh"
        )

    def test_nftables_xblp_forward_chain_present(self) -> None:
        rc, stdout, _ = _run(["nft", "list", "chain", "inet", "xblp", "forward"])
        assert rc == 0, (
            "nftables table inet xblp / chain forward not found. "
            "Apply the Stage 3 nftables ruleset: "
            "nft -f deploy/nftables/xblp.nft.template (with {table} replaced)"
        )
        assert "hook forward" in stdout, (
            f"xblp forward chain does not hook the netfilter forward hook:\n{stdout}"
        )

    def test_br0_has_ip_address(self) -> None:
        rc, stdout, _ = _run(["ip", "-4", "addr", "show", "dev", "br0"])
        assert rc == 0
        assert "inet " in stdout, (
            f"br0 has no IPv4 address (DHCP may not have completed):\n{stdout}"
        )


# ── Live tests (no connected device required) ─────────────────────────────────


class TestBridgeLive:
    def test_internet_connectivity_via_br0(self, br0_ip: str) -> None:
        """Bridge host can reach the internet through br0 (end-to-end bridge path)."""
        rc, _, stderr = _run(["ping", "-c", "3", "-I", "br0", "-W", "3", "8.8.8.8"])
        assert rc == 0, (
            f"ping 8.8.8.8 via br0 failed (rc={rc}). "
            f"Bridge may not be forwarding. br0 IP: {br0_ip}\n{stderr}"
        )

    def test_capture_sees_packets_on_br0(self, br0_ip: str) -> None:
        """live_capture('br0') yields PacketEvents when traffic flows through br0."""
        from xblp_capture.sniffer import live_capture

        events: list[object] = []
        errors: list[BaseException] = []

        def _capture() -> None:
            try:
                for event in live_capture("br0"):
                    events.append(event)
                    if len(events) >= 3:
                        break
            except Exception as exc:  # broad catch: store for assertion below
                errors.append(exc)

        t = threading.Thread(target=_capture, daemon=True)
        t.start()

        # Allow AsyncSniffer to start up
        time.sleep(0.5)

        # Generate traffic on br0 (ARP request for gateway fires even without internet)
        subprocess.run(
            ["ping", "-c", "3", "-I", "br0", "-W", "2", "8.8.8.8"],
            capture_output=True,
            timeout=15,
        )

        # Give the sniffer time to process queued packets
        time.sleep(1)
        t.join(timeout=3)

        if errors:
            pytest.fail(f"live_capture raised: {errors[0]}")

        assert len(events) >= 1, (
            "live_capture('br0') yielded no packets after pinging via br0. "
            "Is scapy installed? Is br0 UP with an address? "
            f"br0 IP: {br0_ip}"
        )


# ── Traffic tests (require a device on the LAN port) ─────────────────────────


@pytest.mark.needs_device
class TestBridgeTraffic:
    """These tests verify nftables is filtering actually-bridged traffic.

    They require a device (Xbox or any other host) connected to the LAN
    (Xbox-facing) port. They are skipped automatically if no device is
    detected via ARP on br0.

    To run after connecting a device:
        sudo .venv/bin/pytest tests/integration/test_bridge.py -v -m needs_device
    """

    def test_nftables_forward_counter_increments(
        self, has_connected_device: bool
    ) -> None:
        """Bridged traffic (Xbox → router) must increment the nftables forward counter."""
        if not has_connected_device:
            pytest.skip(
                "No device detected on LAN port (no carrier on LAN slave). "
                "Plug a device into the Xbox-facing port and re-run."
            )

        # Write a counting rule via nft -f to avoid command-line quoting issues.
        comment = "xblp_stage5_test"
        nft_add = f'add rule inet xblp forward counter comment "{comment}"\n'

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nft", delete=False) as f:
            f.write(nft_add)
            tmpfile = f.name

        try:
            _run_ok(["nft", "-f", tmpfile])
        finally:
            os.unlink(tmpfile)

        # Find the rule handle for cleanup
        handle: str | None = None
        try:
            stdout = _run_ok(["nft", "-a", "list", "chain", "inet", "xblp", "forward"])
            for line in stdout.splitlines():
                if comment in line:
                    m = re.search(r"# handle (\d+)", line)
                    if m:
                        handle = m.group(1)
                    break

            count_before = _extract_counter(stdout, comment)

            # Wait for traffic from the connected device to traverse the bridge
            time.sleep(10)

            stdout_after = _run_ok(
                ["nft", "-a", "list", "chain", "inet", "xblp", "forward"]
            )
            count_after = _extract_counter(stdout_after, comment)

            assert count_after > count_before, (
                f"nftables forward chain packet counter did not increment "
                f"({count_before} → {count_after}). "
                "Is br_netfilter loaded? Is net.bridge.bridge-nf-call-iptables=1? "
                "Is the connected device actually generating traffic?"
            )
        finally:
            if handle:
                _run(["nft", "delete", "rule", "inet", "xblp", "forward", "handle", handle])


def _extract_counter(nft_output: str, comment: str) -> int:
    """Return the packet count from an nft chain listing for a rule with the given comment."""
    for line in nft_output.splitlines():
        if comment in line:
            m = re.search(r"packets (\d+)", line)
            if m:
                return int(m.group(1))
    return 0
