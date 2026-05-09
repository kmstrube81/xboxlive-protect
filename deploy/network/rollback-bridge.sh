#!/usr/bin/env bash
# rollback-bridge.sh — Restore the pre-bridge network configuration.
#
# Modes:
#   --auto   Called by xblp-bridge-rollback.service at early boot when the
#            bridge-pending sentinel is present. Restores config files and
#            exits; the boot continues with the previous network config.
#   (none)   Called manually. Restores files and reboots immediately so the
#            previous config takes effect. (You will lose this SSH session.)
#
# See docs/troubleshooting.md for the full recovery procedure.

set -euo pipefail

SENTINEL=/etc/xboxlive-protect/.bridge-pending
BACKUP_DIR=/etc/network/interfaces.xblp-backup

info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
die()   { error "$*"; exit 1; }

AUTO=0
[[ "${1:-}" == "--auto" ]] && AUTO=1

# ── Sentinel check ────────────────────────────────────────────────────────────
if [[ ! -f "$SENTINEL" ]]; then
    if [[ $AUTO -eq 1 ]]; then
        # ConditionPathExists should have prevented this call, but be safe.
        exit 0
    fi
    warn "No bridge-pending sentinel found at $SENTINEL."
    warn "Bridge was already confirmed, or no bring-up is in progress."
    warn "To restore a backup manually: cp -a $BACKUP_DIR/. /etc/network/ && reboot"
    exit 0
fi

# ── Validate backup ───────────────────────────────────────────────────────────
[[ -f "$BACKUP_DIR/interfaces" ]] || \
    die "Backup not found at $BACKUP_DIR/interfaces. Cannot auto-rollback.
  Restore manually: cp -a $BACKUP_DIR/. /etc/network/ && reboot"

# ── Restore backup ────────────────────────────────────────────────────────────
info "Restoring /etc/network/interfaces from $BACKUP_DIR ..."
cp -a "$BACKUP_DIR/interfaces" /etc/network/interfaces

# Remove the bridge config we wrote, then restore any pre-bridge interfaces.d files.
rm -f /etc/network/interfaces.d/br0.conf
if [[ -d "$BACKUP_DIR/interfaces.d" ]]; then
    cp -a "$BACKUP_DIR/interfaces.d/." /etc/network/interfaces.d/ 2>/dev/null || true
fi

rm -f "$SENTINEL"
info "Sentinel removed."

# ── Mode-specific teardown ────────────────────────────────────────────────────
if [[ $AUTO -eq 1 ]]; then
    info "Auto rollback complete. Boot continues with previous network configuration."
else
    # Cancel any pending shutdown timer, then reboot to apply the old config.
    # The current SSH session will be lost; reconnect after reboot.
    shutdown -c 2>/dev/null || true
    info "Files restored. Rebooting to apply previous network configuration..."
    shutdown -r now
fi
