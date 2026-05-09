"""CLI entrypoint for manual capture validation on target hardware.

This is a development and validation tool, not the production service.
It wires live_capture → PeerScorer and prints results to stdout/stderr
without writing to the database.

Usage (must run as root for raw sockets):
    sudo .venv/bin/python -m xblp_capture \\
        --interface br0 \\
        --xbox-ip 192.168.1.50 \\
        --profile mw2-x360
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

# Resolved relative to the installed source tree; works for editable installs.
# Production daemon will read from /etc/xboxlive-protect/profiles/ instead.
_DEFAULT_PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"

_TICK_INTERVAL = 1.0  # seconds between scorer ticks
_TABLE_INTERVAL = 5.0  # seconds between peer table dumps to stderr
_PRUNE_INTERVAL = 60.0  # seconds between peer state prune passes


def _resolve_profile_path(profile_arg: str, profiles_dir: Path) -> Path:
    direct = Path(profile_arg)
    if direct.exists():
        return direct
    named = profiles_dir / f"{profile_arg}.yaml"
    if named.exists():
        return named
    print(
        f"error: profile {profile_arg!r} not found (tried {direct} and {named})",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m xblp_capture",
        description="Capture and score Xbox Live peer traffic (manual testing tool).",
    )
    parser.add_argument(
        "--interface",
        required=True,
        metavar="IFACE",
        help="Network interface to sniff (e.g. br0, eth0)",
    )
    parser.add_argument(
        "--xbox-ip",
        required=True,
        metavar="IP",
        help="Xbox IP address on the local network",
    )
    parser.add_argument(
        "--profile",
        required=True,
        metavar="ID_OR_PATH",
        help="Profile ID (e.g. mw2-x360) or path to a YAML file",
    )
    parser.add_argument(
        "--profiles-dir",
        metavar="DIR",
        default=str(_DEFAULT_PROFILES_DIR),
        help="Directory to search for profiles by ID (default: repo profiles/)",
    )
    args = parser.parse_args()

    from xblp_capture.scorer import PeerScorer
    from xblp_capture.sniffer import live_capture
    from xblp_common.profiles import load_profile

    profile_path = _resolve_profile_path(args.profile, Path(args.profiles_dir))
    profile = load_profile(profile_path)
    scorer = PeerScorer(xbox_ip=args.xbox_ip, profile=profile)

    log.info(
        "capture starting",
        interface=args.interface,
        xbox_ip=args.xbox_ip,
        profile=profile.id,
        min_pps=profile.detection.min_pps,
        window_seconds=profile.detection.window_seconds,
        min_consecutive_windows=profile.detection.min_consecutive_windows,
    )

    shutdown = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal shutdown
        shutdown = True
        log.info("shutdown requested", signal=signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    last_tick = time.monotonic()
    last_table = time.monotonic()
    last_prune = time.monotonic()

    for event in live_capture(args.interface, bpf_filter=f"host {args.xbox_ip}"):
        if shutdown:
            break

        scorer.observe(event)

        now_mono = time.monotonic()
        now_epoch = time.time()

        if now_mono - last_tick >= _TICK_INTERVAL:
            for host in scorer.tick(now_epoch):
                print(
                    f"[DETECTED] ip={host.ip}  score={host.score:.1f}"
                    f"  duration={host.duration_seconds:.1f}s",
                    flush=True,
                )
            last_tick = now_mono

        if now_mono - last_table >= _TABLE_INTERVAL:
            table = scorer.peer_table(now_epoch)
            print(
                f"\n--- peer table ({len(table)} peers) ---",
                file=sys.stderr,
                flush=True,
            )
            for peer in table:
                print(
                    f"  {peer.ip:>20}  pps={peer.packets_per_second:6.1f}"
                    f"  score={peer.score:8.1f}  qual={peer.qualified_recent_windows}",
                    file=sys.stderr,
                )
            last_table = now_mono

        if now_mono - last_prune >= _PRUNE_INTERVAL:
            scorer.prune(now_epoch)
            last_prune = now_mono

    log.info("capture finished")


if __name__ == "__main__":
    main()
