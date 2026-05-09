#!/usr/bin/env bash
# bring-up-bridge.sh — Configure the R4S as a transparent L2 bridge.
#
# SAFETY: This script arms a 10-minute reboot timer before touching network
# config. If you cannot SSH back in within 10 minutes, the device reboots
# automatically and restores the previous network configuration.
#
# Usage:
#   sudo bash deploy/network/bring-up-bridge.sh [OPTIONS]
#
# Options:
#   --wan IFACE  Router-facing interface (default: auto-detect via default route)
#   --lan IFACE  Xbox-facing interface   (default: the other physical Ethernet NIC)
#   --yes        Skip confirmation prompt (for non-interactive use)
#   -h, --help   Show this help
#
# After bring-up, SSH back in via xboxlive-protect.local and run:
#   sudo deploy/network/confirm-bridge.sh
#
# See docs/troubleshooting.md for recovery procedures.

set -euo pipefail

SENTINEL=/etc/xboxlive-protect/.bridge-pending
BACKUP_DIR=/etc/network/interfaces.xblp-backup
ROLLBACK_INSTALL_DIR=/usr/local/lib/xboxlive-protect
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIRM_MINUTES=10

# ── Output helpers ────────────────────────────────────────────────────────────
info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
die()   { error "$*"; exit 1; }
hr()    { printf '%s\n' "────────────────────────────────────────────────────────────"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
WAN_IFACE=""
LAN_IFACE=""
YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wan)      WAN_IFACE="$2"; shift 2 ;;
        --lan)      LAN_IFACE="$2"; shift 2 ;;
        --yes)      YES=1; shift ;;
        -h|--help)  sed -n '/^# /s/^# \?//p' "$0" | head -20; exit 0 ;;
        *)          die "Unknown argument: '$1'. Run with --help for usage." ;;
    esac
done

hr
info "xboxlive-protect bridge bring-up"
hr

# ── Step 1: Root ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root (sudo)."

# ── Step 2: Enumerate physical Ethernet interfaces ────────────────────────────
mapfile -t ETH_IFACES < <(
    for d in /sys/class/net/*/; do
        iface="$(basename "$d")"
        [[ "$iface" == "lo" ]] && continue
        [[ -e "${d}device" ]] || continue          # skip virtual (bridges, veth, vlan, etc.)
        [[ "$(cat "${d}type" 2>/dev/null)" == "1" ]] || continue   # Ethernet type = 1
        printf '%s\n' "$iface"
    done | sort
)
info "Physical Ethernet interfaces found: ${ETH_IFACES[*]:-none}"
[[ ${#ETH_IFACES[@]} -ge 2 ]] || \
    die "Need at least 2 physical Ethernet interfaces; found ${#ETH_IFACES[@]}: ${ETH_IFACES[*]:-none}."

# ── Step 3: Pending-bring-up guard ───────────────────────────────────────────
# Prevent re-entry while a previous bring-up is in progress.
if [[ -f "$SENTINEL" ]]; then
    die "A bridge bring-up is already in progress (sentinel at $SENTINEL).
  To confirm: SSH back in via xboxlive-protect.local and run confirm-bridge.sh
  To undo:    sudo $ROLLBACK_INSTALL_DIR/rollback-bridge.sh   (then reboot)"
fi

# ── Step 4: Idempotency check ─────────────────────────────────────────────────
if ip link show br0 &>/dev/null; then
    ACTUAL_MEMBERS=()
    for d in /sys/class/net/br0/brif/*/; do
        [[ -d "$d" ]] || continue
        ACTUAL_MEMBERS+=("$(basename "$d")")
    done

    if [[ "${#ACTUAL_MEMBERS[@]}" -eq 2 ]]; then
        # Check against explicit --wan/--lan if provided
        OK=1
        [[ -n "$WAN_IFACE" && " ${ACTUAL_MEMBERS[*]} " != *" $WAN_IFACE "* ]] && OK=0
        [[ -n "$LAN_IFACE" && " ${ACTUAL_MEMBERS[*]} " != *" $LAN_IFACE "* ]] && OK=0
        if [[ $OK -eq 1 ]]; then
            info "br0 already configured with members: ${ACTUAL_MEMBERS[*]}. Nothing to do."
            exit 0
        fi
        die "br0 exists with members ${ACTUAL_MEMBERS[*]} but they don't match --wan=${WAN_IFACE:-?} --lan=${LAN_IFACE:-?}.
  See docs/troubleshooting.md."
    fi

    die "br0 already exists with unexpected member count (${#ACTUAL_MEMBERS[@]}): ${ACTUAL_MEMBERS[*]:-none}.
  Manual recovery required — see docs/troubleshooting.md."
fi

# ── Step 5: Resolve WAN interface (FAIL LOUD on any ambiguity) ───────────────
if [[ -z "$WAN_IFACE" ]]; then
    mapfile -t DEFAULT_ROUTES < <(ip route show default 2>/dev/null | grep -E '^default' || true)

    [[ ${#DEFAULT_ROUTES[@]} -gt 0 ]] || \
        die "No default route found. Cannot auto-detect WAN interface.
  Pass --wan IFACE and --lan IFACE explicitly."

    [[ ${#DEFAULT_ROUTES[@]} -eq 1 ]] || \
        die "Multiple default routes found (${#DEFAULT_ROUTES[@]}). Network state is ambiguous.
  Resolve to a single default route and retry, or pass --wan and --lan explicitly."

    DETECTED_WAN="$(printf '%s\n' "${DEFAULT_ROUTES[0]}" | \
        awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"

    [[ -n "$DETECTED_WAN" ]] || \
        die "Could not parse interface from default route: '${DEFAULT_ROUTES[0]}'.
  Pass --wan and --lan explicitly."

    found=0
    for iface in "${ETH_IFACES[@]}"; do
        [[ "$iface" == "$DETECTED_WAN" ]] && found=1 && break
    done
    [[ $found -eq 1 ]] || \
        die "Default route is via '$DETECTED_WAN' which is not a physical Ethernet interface.
  Found physical Ethernet: ${ETH_IFACES[*]}
  If the current NIC is a bond/bridge/vlan, pass --wan and --lan explicitly."

    WAN_IFACE="$DETECTED_WAN"
    info "WAN interface auto-detected: $WAN_IFACE (has default route)"
else
    found=0
    for iface in "${ETH_IFACES[@]}"; do
        [[ "$iface" == "$WAN_IFACE" ]] && found=1 && break
    done
    [[ $found -eq 1 ]] || \
        die "--wan '$WAN_IFACE' is not a physical Ethernet interface.
  Available physical Ethernet: ${ETH_IFACES[*]}"
    info "WAN interface (explicit): $WAN_IFACE"
fi

# ── Step 6: Resolve LAN interface ─────────────────────────────────────────────
if [[ -z "$LAN_IFACE" ]]; then
    OTHER_IFACES=()
    for iface in "${ETH_IFACES[@]}"; do
        [[ "$iface" == "$WAN_IFACE" ]] && continue
        OTHER_IFACES+=("$iface")
    done
    [[ ${#OTHER_IFACES[@]} -eq 1 ]] || \
        die "Expected exactly 1 LAN (non-WAN) Ethernet interface, found ${#OTHER_IFACES[@]}: ${OTHER_IFACES[*]:-none}.
  Pass --lan explicitly."
    LAN_IFACE="${OTHER_IFACES[0]}"
    info "LAN interface auto-detected: $LAN_IFACE (Xbox-facing)"
else
    [[ "$LAN_IFACE" != "$WAN_IFACE" ]] || \
        die "--lan and --wan cannot be the same interface ($WAN_IFACE)."
    found=0
    for iface in "${ETH_IFACES[@]}"; do
        [[ "$iface" == "$LAN_IFACE" ]] && found=1 && break
    done
    [[ $found -eq 1 ]] || \
        die "--lan '$LAN_IFACE' is not a physical Ethernet interface.
  Available physical Ethernet: ${ETH_IFACES[*]}"
    info "LAN interface (explicit): $LAN_IFACE"
fi

# ── Step 7: Install bridge-utils ─────────────────────────────────────────────
# Must succeed before any destructive change; safe to retry if it fails here.
if ! command -v brctl &>/dev/null; then
    info "Installing bridge-utils..."
    apt-get install -y bridge-utils || \
        die "bridge-utils installation failed. Fix apt errors and retry.
  No network configuration has been modified."
    info "bridge-utils installed."
fi

# ── Step 8: Load br_netfilter ─────────────────────────────────────────────────
# Required before writing the sysctl (the sysctl key doesn't exist until the
# module is loaded). Also enables nftables on bridged traffic immediately.
info "Loading br_netfilter module..."
modprobe br_netfilter || die "modprobe br_netfilter failed. Check kernel module availability."

# ── Step 9: Install kernel config for persistence ─────────────────────────────
info "Installing /etc/modules-load.d/xblp-bridge.conf..."
install -m 644 "$SCRIPT_DIR/modules-br_netfilter.conf" /etc/modules-load.d/xblp-bridge.conf

info "Applying bridge sysctls..."
sysctl -w net.bridge.bridge-nf-call-iptables=1 >/dev/null
sysctl -w net.bridge.bridge-nf-call-ip6tables=0 >/dev/null

info "Installing /etc/sysctl.d/99-xblp-bridge.conf..."
install -m 644 "$SCRIPT_DIR/sysctl-bridge.conf" /etc/sysctl.d/99-xblp-bridge.conf

# ── Step 10: SSH session info (informational) ─────────────────────────────────
if [[ -n "${SSH_CONNECTION:-}" ]]; then
    SSH_CLIENT_IP="$(printf '%s\n' "$SSH_CONNECTION" | awk '{print $1}')"
    SSH_IFACE="$(ip route get "$SSH_CLIENT_IP" 2>/dev/null | \
        awk '/dev/{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' || true)"
    info "Current SSH session: from $SSH_CLIENT_IP${SSH_IFACE:+ via $SSH_IFACE}"
    info "After bridge is up, reconnect via:  ssh <user>@xboxlive-protect.local"
fi

# ── Step 11: Confirmation prompt (before arming the timer) ───────────────────
hr
printf '\n'
info "Proposed bridge configuration:"
info "  WAN (router / network side): $WAN_IFACE"
info "  LAN (Xbox-facing side):      $LAN_IFACE"
printf '\n'
info "This script will:"
info "  1. Back up /etc/network config to $BACKUP_DIR"
info "  2. Arm a ${CONFIRM_MINUTES}-minute auto-reboot safety timer"
info "  3. Configure br0 with DHCP and restart networking (~3 s delay)"
info "  4. Drop this SSH session (expected)"
printf '\n'
warn "If you cannot reconnect within ${CONFIRM_MINUTES} minutes, the device will"
warn "reboot itself and restore the previous network configuration."
printf '\n'

if [[ $YES -eq 0 ]]; then
    read -r -p "Proceed? (type 'yes' to continue): " CONFIRM
    [[ "$CONFIRM" == "yes" ]] || { info "Aborted. No changes made."; exit 0; }
fi

# ── Destructive sequence begins ───────────────────────────────────────────────
hr
info "Starting bridge configuration..."

# ── Step 12: Backup ───────────────────────────────────────────────────────────
info "Backing up /etc/network config to $BACKUP_DIR ..."
mkdir -p "$BACKUP_DIR" "$BACKUP_DIR/interfaces.d"
cp -a /etc/network/interfaces "$BACKUP_DIR/interfaces"
shopt -s nullglob
for f in /etc/network/interfaces.d/*; do
    cp -a "$f" "$BACKUP_DIR/interfaces.d/"
done
shopt -u nullglob
info "Backup complete."

# ── Step 13: Sentinel + rollback infrastructure ───────────────────────────────
info "Creating /etc/xboxlive-protect/ ..."
mkdir -p /etc/xboxlive-protect

info "Writing bridge-pending sentinel..."
touch "$SENTINEL"

info "Installing rollback script to $ROLLBACK_INSTALL_DIR ..."
mkdir -p "$ROLLBACK_INSTALL_DIR"
install -m 755 "$SCRIPT_DIR/rollback-bridge.sh" "$ROLLBACK_INSTALL_DIR/rollback-bridge.sh"

info "Installing xblp-bridge-rollback.service..."
install -m 644 "$SCRIPT_DIR/xblp-bridge-rollback.service" \
    /etc/systemd/system/xblp-bridge-rollback.service
systemctl daemon-reload
systemctl enable xblp-bridge-rollback.service
info "Rollback service enabled."

# ── Step 14: Arm reboot timer ─────────────────────────────────────────────────
info "Arming ${CONFIRM_MINUTES}-minute safety reboot timer..."
if shutdown -r "+${CONFIRM_MINUTES}" \
        "xboxlive-protect: auto-reboot to restore network config if bridge not confirmed" \
        2>/dev/null; then
    info "Auto-reboot armed. Cancel with: sudo shutdown -c"
else
    warn "Could not arm auto-reboot timer (shutdown -r failed). Proceed carefully."
fi

# ── Step 15: Write new network config ─────────────────────────────────────────
info "Removing conflicting per-interface configs from /etc/network/interfaces.d/ ..."
shopt -s nullglob
for f in /etc/network/interfaces.d/*; do
    [[ -f "$f" ]] || continue
    if grep -qEm1 "^\s*(iface|auto|allow-[^[:space:]]+)\s+(${WAN_IFACE}|${LAN_IFACE})\b" \
            "$f" 2>/dev/null; then
        info "  Removing $f"
        rm "$f"
    fi
done
shopt -u nullglob

info "Removing conflicting stanzas from /etc/network/interfaces ..."
python3 - "$WAN_IFACE" "$LAN_IFACE" <<'PYEOF'
import re, sys
from pathlib import Path

skip_ifaces = set(sys.argv[1:])
p = Path('/etc/network/interfaces')
lines = p.read_text().splitlines(keepends=True)
out: list[str] = []
skip = False
for line in lines:
    if line and line[0] in (' ', '\t'):
        # Continuation line (leading whitespace) — belongs to the current stanza.
        if skip:
            continue
    else:
        m = re.match(r'^(iface|auto|allow-\S+|mapping)\s+(\S+)', line.strip())
        if m:
            skip = m.group(2) in skip_ifaces
            if skip:
                continue
        elif not line.strip():
            skip = False
    if not skip:
        out.append(line)
p.write_text(''.join(out))
PYEOF

info "Ensuring /etc/network/interfaces sources interfaces.d ..."
if ! grep -qE '^source(-directory)?\s+/etc/network/interfaces\.d' /etc/network/interfaces; then
    printf '\nsource-directory /etc/network/interfaces.d\n' >> /etc/network/interfaces
fi

info "Writing /etc/network/interfaces.d/br0.conf ..."
sed \
    -e "s/__WAN_IFACE__/${WAN_IFACE}/g" \
    -e "s/__LAN_IFACE__/${LAN_IFACE}/g" \
    "$SCRIPT_DIR/br0.conf" > /etc/network/interfaces.d/br0.conf

# ── Nftables sanity note ───────────────────────────────────────────────────────
if ! nft list chain inet xblp forward &>/dev/null 2>&1; then
    warn ""
    warn "nftables table inet xblp not found. The bridge will forward traffic"
    warn "but the blocklist will not be active until the Stage 3 nftables"
    warn "ruleset is applied. Run that after confirming the bridge."
    warn ""
fi

# ── Step 16: Schedule detached networking restart ────────────────────────────
hr
info "Network will restart in 3 seconds. This SSH session will disconnect."
printf '\n'
info "Reconnect after ~30 seconds:"
info "  ssh <user>@xboxlive-protect.local"
info "  sudo deploy/network/confirm-bridge.sh"
printf '\n'
warn "If you cannot reconnect within ${CONFIRM_MINUTES} minutes, the device will"
warn "reboot itself and restore the previous network configuration."
hr

nohup bash -c 'sleep 3 && systemctl restart networking' >/dev/null 2>&1 &
disown $!
exit 0
