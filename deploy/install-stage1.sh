#!/usr/bin/env bash
# install-stage1.sh — Wire up nginx + TLS on an existing Debian system.
#
# This script sets up the system layer (nginx, systemd units, service user)
# and creates /opt symlinks for the Stage 1 dev layout (see step 1 below).
# It is idempotent — safe to re-run on an already-installed system.
#
# Prerequisite: a Python virtualenv must exist at $XBLP_VENV (see below).
# The script fails loudly if it is absent.
#
# Environment overrides:
#   XBLP_SRC_ROOT  Source checkout root (default: parent directory of this script)
#   XBLP_VENV      Python virtualenv    (default: $XBLP_SRC_ROOT/.venv)
#
# Usage (as root or via sudo):
#   sudo bash deploy/install-stage1.sh
#
# This script is the authoritative spec for Phase 5's SD image builder.

set -euo pipefail
trap 'echo "ERROR: install-stage1.sh failed at line $LINENO" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

XBLP_SRC_ROOT="${XBLP_SRC_ROOT:-$REPO_DIR}"
XBLP_VENV="${XBLP_VENV:-$XBLP_SRC_ROOT/.venv}"

SERVICE_USER=xblp
INSTALL_DIR=/opt/xboxlive-protect
STATE_DIR=/var/lib/xboxlive-protect

# ── Helpers ───────────────────────────────────────────────────────────────────

_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
_red()    { printf '\033[31m%s\033[0m\n' "$*"; }
_step()   { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

_die() { _red "ERROR: $*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    _die "Run as root or with sudo."
fi

# ── 1. /opt symlinks (Stage 1 dev layout) ────────────────────────────────────
# Stage 1 uses symlinks so the production path /opt/xboxlive-protect/ resolves
# to the dev checkout without duplicating files. Phase 5's image builder will
# REPLACE these symlinks with a real /opt/xboxlive-protect/ tree containing
# extracted release artifacts; the systemd unit (ExecStart=.../venv/bin/python)
# stays unchanged across that transition.

_step "Creating /opt/xboxlive-protect symlinks (Stage 1 dev layout)"

[[ -d "$XBLP_SRC_ROOT" ]] || _die "Source root not found: $XBLP_SRC_ROOT"
[[ -d "$XBLP_VENV" ]]     || _die "Virtualenv not found: $XBLP_VENV (create it or set XBLP_VENV)"

mkdir -p "$(dirname "$INSTALL_DIR")"

# Primary symlink: /opt/xboxlive-protect → $XBLP_SRC_ROOT
ln -sfn "$XBLP_SRC_ROOT" "$INSTALL_DIR"
_green "  $INSTALL_DIR → $XBLP_SRC_ROOT"

# Secondary symlink: because $INSTALL_DIR is itself a symlink, this command
# physically creates $XBLP_SRC_ROOT/venv (not a file under /opt). Consequence:
# `rm -rf /opt/xboxlive-protect` unlinks the top-level symlink only — it does
# not recurse into or affect the dev checkout.
# Order is strict: the primary symlink must exist before this ln resolves the path.
ln -sfn "$XBLP_VENV" "$INSTALL_DIR/venv"
_green "  $XBLP_SRC_ROOT/venv → $XBLP_VENV"

[[ -x "$INSTALL_DIR/venv/bin/python" ]] || \
    _die "Python not executable at $INSTALL_DIR/venv/bin/python — check XBLP_VENV"
_green "  python OK: $(readlink -f "$INSTALL_DIR/venv/bin/python")"

# ── 2. Dependencies ───────────────────────────────────────────────────────────

_step "Installing nginx"
apt-get install -y nginx

# ── 3. Service user ───────────────────────────────────────────────────────────

_step "Ensuring service user '$SERVICE_USER' exists"
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    _green "  created user $SERVICE_USER"
else
    _yellow "  user $SERVICE_USER already exists, skipping"
fi

# ── 4. State directory ────────────────────────────────────────────────────────
# systemd's StateDirectory= also creates this, but we do it here so the
# install script is self-contained (can run before the unit file is loaded).

_step "Ensuring state directory $STATE_DIR"
mkdir -p "$STATE_DIR"
# chown -R is unconditional and idempotent — it also fixes any files inside
# the directory that were created by a previous root-owned daemon run.
chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
chmod 750 "$STATE_DIR"
_green "  $STATE_DIR owned by $SERVICE_USER (recursive)"

# ── 5. nginx config ───────────────────────────────────────────────────────────

_step "Installing nginx config"
cp "$REPO_DIR/deploy/nginx/xblp.conf" /etc/nginx/sites-available/xblp
ln -sf /etc/nginx/sites-available/xblp /etc/nginx/sites-enabled/xblp
rm -f /etc/nginx/sites-enabled/default
_green "  xblp site enabled, default site disabled"

# ── 6. systemd units ──────────────────────────────────────────────────────────

_step "Installing systemd units"

cp "$REPO_DIR/deploy/systemd/xblp-api.service" /etc/systemd/system/xblp-api.service

mkdir -p /etc/systemd/system/nginx.service.d
cp "$REPO_DIR/deploy/systemd/nginx.service.d/xblp.conf" \
   /etc/systemd/system/nginx.service.d/xblp.conf

systemctl daemon-reload
_green "  units installed and daemon reloaded"

# ── 7. Start / enable xblp-api ───────────────────────────────────────────────

_step "Enabling and starting xblp-api"
systemctl enable --now xblp-api.service
_green "  xblp-api enabled"

# Wait for the TLS cert to appear (generated during daemon lifespan startup).
_step "Waiting for TLS cert generation"
for i in $(seq 1 30); do
    if [[ -f "$STATE_DIR/cert.pem" && -f "$STATE_DIR/key.pem" ]]; then
        _green "  cert ready after ${i}s"
        break
    fi
    if [[ $i -eq 30 ]]; then
        printf 'ERROR: xblp-api did not start. Last 20 journalctl lines:\n' >&2
        journalctl -u xblp-api -n 20 --no-pager >&2
        exit 1
    fi
    sleep 1
done

# Verify nginx can read the cert (catches unexpected ownership even after chown -R).
if ! runuser -u "$SERVICE_USER" -- test -r "$STATE_DIR/cert.pem"; then
    printf 'ERROR: cert.pem not readable by %s. Last 20 journalctl lines:\n' "$SERVICE_USER" >&2
    journalctl -u xblp-api -n 20 --no-pager >&2
    exit 1
fi
_green "  cert readable by $SERVICE_USER"

# ── 8. Validate and start nginx ───────────────────────────────────────────────

_step "Validating nginx config"
nginx -t

_step "Enabling and starting nginx"
systemctl enable --now nginx.service
systemctl restart nginx.service
_green "  nginx running"

# ── Done ──────────────────────────────────────────────────────────────────────

printf '\n'
_green "======================================================================"
_green " Install complete."
_green " Open https://xboxlive-protect.local in a browser."
_green " Accept the self-signed cert warning (one-time)."
_green " Default credentials: admin / xboxlive-protect"
_green " Change the password immediately."
_green "======================================================================"
