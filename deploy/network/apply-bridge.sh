#!/usr/bin/env bash
# apply-bridge.sh — Perform the destructive networking restart for the bridge
# bring-up. Invoked by bring-up-bridge.sh via `systemd-run --no-block` as a
# transient unit so it survives the SSH session's termination.
#
# Why a separate script: passing a multi-line bash body through
# `systemd-run /bin/bash -c "..."` is fragile (argument quoting, redirection
# parsing, no journal record of what actually ran). A dedicated script gives
# systemd-run a clean argv (three tokens: program, $1, $2), verbose logging
# via `set -x`, and a debuggable surface — you can run this script directly
# without bring-up-bridge.sh.
#
# Usage (typically invoked by systemd-run, not directly):
#   apply-bridge.sh WAN_IFACE LAN_IFACE
#
# Side effects:
#   - kills any dhclient bound to WAN_IFACE or LAN_IFACE
#   - flushes IPs from WAN_IFACE and LAN_IFACE
#   - restarts networking.service (which re-runs `ifup -a` and brings up br0)
#
# This script must NOT use `set -e` — each cleanup step is best-effort, and
# we want every command logged via `set -x` so the journal captures the full
# sequence even if one step's exit code is nonzero.

set -x

WAN_IFACE="${1:?WAN_IFACE required as first argument}"
LAN_IFACE="${2:?LAN_IFACE required as second argument}"

# Give the parent SSH session a beat to return cleanly before we yank the
# IP out from under it. Without this the user sees the script exit on a
# half-rendered prompt.
sleep 3

# Kill any dhclient processes tracking the soon-to-be-slave NICs. Without
# this, dhclient holds the IP and the slave attach to br0 silently fails
# (or works but the IP lingers as we saw in the first Stage 5 attempt).
pkill -f "dhclient.*${WAN_IFACE}" || true
pkill -f "dhclient.*${LAN_IFACE}" || true

# Flush any remaining IP addresses from the slaves. After this point the
# SSH session is dead.
ip addr flush dev "${WAN_IFACE}" || true
ip addr flush dev "${LAN_IFACE}" || true

# Restart networking so ifupdown re-reads /etc/network/interfaces.d/ and
# brings up br0 per the new config. This is the moment the bridge is born.
systemctl restart networking

# Reload avahi-daemon so it re-evaluates its interface list and re-announces
# the host's mDNS records on br0 (the new management interface). Without this,
# avahi may still believe it's announcing on the old per-NIC interface, or
# may already have hit a host-name conflict against its own stale state and
# fallen back to xboxlive-protect-2.local. Reload (not restart) preserves
# already-registered services without churn.
systemctl reload avahi-daemon || systemctl restart avahi-daemon || true

# Report final state to the journal for diagnostic purposes.
ip -brief addr show
ip -brief link show

exit 0
