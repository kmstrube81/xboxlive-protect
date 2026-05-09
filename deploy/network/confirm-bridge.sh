#!/usr/bin/env bash
# confirm-bridge.sh — Confirm the bridge is working after bring-up-bridge.sh.
#
# Run this after SSHing back in via xboxlive-protect.local. It:
#   1. Cancels the pending auto-reboot (best-effort — secondary concern)
#   2. Removes the bridge-pending sentinel (primary job — disarms rollback)
#
# The xblp-bridge-rollback.service remains installed and enabled, but with no
# sentinel it permanently skips its ExecStart on every future boot.

set -euo pipefail

SENTINEL=/etc/xboxlive-protect/.bridge-pending

info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
die()   { error "$*"; exit 1; }

[[ $EUID -eq 0 ]] || die "Must run as root (sudo)."

if [[ ! -f "$SENTINEL" ]]; then
    warn "No bridge-pending sentinel found at $SENTINEL."
    warn "Bridge was already confirmed, or no bring-up is in progress."
    exit 0
fi

# Cancel the auto-reboot timer (best-effort). May fail if: the timer already
# fired, logind restarted, or no reboot was pending. The primary job — removing
# the sentinel — proceeds regardless.
info "Cancelling auto-reboot timer..."
if shutdown -c 2>/dev/null; then
    info "Auto-reboot cancelled."
else
    warn "shutdown -c returned non-zero (no pending reboot, or already cancelled). Continuing."
fi

info "Removing bridge-pending sentinel..."
rm -f "$SENTINEL"

info ""
info "Bridge confirmed successfully."
info "xblp-bridge-rollback.service remains installed but is permanently disarmed"
info "(ConditionPathExists=/etc/xboxlive-protect/.bridge-pending — sentinel gone)."
