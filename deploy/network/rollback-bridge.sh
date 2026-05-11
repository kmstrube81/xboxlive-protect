#!/usr/bin/env bash
# rollback-bridge.sh — Restore the pre-bridge network configuration.
#
# Modes:
#   --auto   Called by xblp-bridge-rollback.service at early boot when the
#            bridge-pending sentinel is present. Restores config files and
#            exits; the boot continues with the previous network config.
#   (none)   Called manually. Restores files, tears down the live bridge,
#            and reboots so the previous config takes effect. (You will
#            lose this SSH session at reboot.)
#
# This script is authoritative over /etc/network/interfaces.d/ — it mirrors
# the backup directory exactly (any files added since bring-up are removed,
# any files present in the backup are restored). It also removes the
# persistent br_netfilter module-load drop-in and sysctl drop-in installed
# by bring-up-bridge.sh, and tears down the live br0 interface (manual mode
# only).
#
# See docs/troubleshooting.md for the full recovery procedure.

set -euo pipefail

SENTINEL=/etc/xboxlive-protect/.bridge-pending
BACKUP_DIR=/etc/network/interfaces.xblp-backup
MODULES_LOAD_DROPIN=/etc/modules-load.d/xblp-bridge.conf
SYSCTL_DROPIN=/etc/sysctl.d/99-xblp-bridge.conf
ROLLBACK_SERVICE=/etc/systemd/system/xblp-bridge-rollback.service
ROLLBACK_HELPER_DIR=/usr/local/lib/xboxlive-protect

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
    warn "If you still want to tear down the bridge, re-run this script after"
    warn "creating the sentinel manually: sudo touch $SENTINEL"
    exit 0
fi

# ── Validate backup ───────────────────────────────────────────────────────────
[[ -f "$BACKUP_DIR/interfaces" ]] || \
    die "Backup not found at $BACKUP_DIR/interfaces. Cannot auto-rollback.
  Inspect $BACKUP_DIR manually and restore /etc/network/ by hand."

# ── Restore /etc/network/interfaces ───────────────────────────────────────────
info "Restoring /etc/network/interfaces from $BACKUP_DIR ..."
cp -a "$BACKUP_DIR/interfaces" /etc/network/interfaces

# ── Mirror /etc/network/interfaces.d/ to match the backup exactly ─────────────
# This is the critical fix: a plain `cp` only adds files, leaving any files
# the bring-up dropped in (br0.conf and potentially others) in place. We need
# the live directory to exactly match the backup directory's contents.
info "Mirroring /etc/network/interfaces.d/ to backup state ..."
mkdir -p /etc/network/interfaces.d

if command -v rsync >/dev/null 2>&1; then
    if [[ -d "$BACKUP_DIR/interfaces.d" ]]; then
        rsync -a --delete \
            "$BACKUP_DIR/interfaces.d/" \
            /etc/network/interfaces.d/
    else
        # Backup didn't capture an interfaces.d (older bring-up?). Empty the
        # live dir to match — there was nothing here pre-bring-up.
        find /etc/network/interfaces.d -mindepth 1 -delete
    fi
else
    # rsync not available — emulate --delete with find, then copy backup in.
    find /etc/network/interfaces.d -mindepth 1 -delete
    if [[ -d "$BACKUP_DIR/interfaces.d" ]]; then
        cp -a "$BACKUP_DIR/interfaces.d/." /etc/network/interfaces.d/ \
            2>/dev/null || true
    fi
fi

# ── Remove persistent system drop-ins installed by bring-up ───────────────────
# These persist across reboots if not removed, and would cause br_netfilter
# to auto-load and the bridge-nf sysctl to be re-applied even after rollback.
info "Removing persistent module-load and sysctl drop-ins ..."
rm -f "$MODULES_LOAD_DROPIN"
rm -f "$SYSCTL_DROPIN"

# ── Remove the rollback service itself (disarmed state) ───────────────────────
# The service is no-op without the sentinel (ConditionPathExists), but with
# the sentinel and drop-ins gone, the service has no remaining purpose.
# Leave the service in place during --auto (we're early in boot, systemd is
# mid-startup; touching units now is messy). Clean it up in manual mode only.
if [[ $AUTO -eq 0 ]]; then
    if [[ -f "$ROLLBACK_SERVICE" ]]; then
        info "Disabling and removing xblp-bridge-rollback.service ..."
        systemctl disable xblp-bridge-rollback.service 2>/dev/null || true
        rm -f "$ROLLBACK_SERVICE"
        systemctl daemon-reload 2>/dev/null || true
    fi
    rm -rf "$ROLLBACK_HELPER_DIR" 2>/dev/null || true
fi

# ── Remove sentinel ───────────────────────────────────────────────────────────
rm -f "$SENTINEL"
info "Sentinel removed."

# ── Mode-specific teardown ────────────────────────────────────────────────────
if [[ $AUTO -eq 1 ]]; then
    info "Auto rollback complete. Boot continues with previous network configuration."
    exit 0
fi

# Manual mode: tear down the live bridge state before rebooting, so the
# kernel state matches the restored config files.

info "Tearing down live bridge state ..."

# Stop any dhclient instances tracking br0.
pkill -f 'dhclient.*br0' 2>/dev/null || true

# Detach slave NICs from br0 (best-effort; they'll be detached anyway when
# we delete br0, but this is explicit).
for slave in eth0 eth1; do
    if [[ -d "/sys/class/net/$slave" ]]; then
        ip link set "$slave" nomaster 2>/dev/null || true
    fi
done

# Bring br0 down and delete it.
if [[ -d /sys/class/net/br0 ]]; then
    ip link set br0 down 2>/dev/null || true
    ip link delete br0 type bridge 2>/dev/null || true
fi

# Unload br_netfilter (won't auto-reload because the drop-in is gone).
modprobe -r br_netfilter 2>/dev/null || true

# Reset the running-kernel sysctls (the drop-in is gone, but the values are
# still set in the kernel until reboot or explicit reset).
sysctl -w net.bridge.bridge-nf-call-iptables=0 >/dev/null 2>&1 || true
sysctl -w net.bridge.bridge-nf-call-ip6tables=0 >/dev/null 2>&1 || true

# Cancel any pending shutdown timer set by bring-up.
shutdown -c 2>/dev/null || true

info "Files restored, live bridge torn down."
info "Rebooting to apply previous network configuration ..."
info "Your SSH session will end. Reconnect via xboxlive-protect.local after reboot."
shutdown -r now
