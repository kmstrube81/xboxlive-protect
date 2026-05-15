#!/usr/bin/env bash
# install-stage1.sh — Wire up nginx + TLS on an existing Debian system.
#
# Prerequisite: the Python package must already be installed at
#   /opt/xboxlive-protect  (virtualenv at /opt/xboxlive-protect/venv)
# This script handles the system layer only: nginx, systemd units, and the
# service user. It is idempotent — safe to re-run on an already-installed
# system.
#
# Usage (as root or via sudo):
#   sudo bash deploy/install-stage1.sh
#
# This script is the authoritative spec for Phase 5's SD image builder.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

# ── 1. Dependencies ───────────────────────────────────────────────────────────

_step "Installing nginx"
apt-get install -y nginx

# ── 2. Service user ───────────────────────────────────────────────────────────

_step "Ensuring service user '$SERVICE_USER' exists"
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    _green "  created user $SERVICE_USER"
else
    _yellow "  user $SERVICE_USER already exists, skipping"
fi

# ── 3. State directory ────────────────────────────────────────────────────────
# systemd's StateDirectory= also creates this, but we do it here so the
# install script is self-contained (can run before the unit file is loaded).

_step "Ensuring state directory $STATE_DIR"
mkdir -p "$STATE_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
chmod 750 "$STATE_DIR"
_green "  $STATE_DIR owned by $SERVICE_USER"

# ── 4. nginx config ───────────────────────────────────────────────────────────

_step "Installing nginx config"
cp "$REPO_DIR/deploy/nginx/xblp.conf" /etc/nginx/sites-available/xblp
ln -sf /etc/nginx/sites-available/xblp /etc/nginx/sites-enabled/xblp
rm -f /etc/nginx/sites-enabled/default
_green "  xblp site enabled, default site disabled"

# ── 5. systemd units ──────────────────────────────────────────────────────────

_step "Installing systemd units"

cp "$REPO_DIR/deploy/systemd/xblp-api.service" /etc/systemd/system/xblp-api.service

mkdir -p /etc/systemd/system/nginx.service.d
cp "$REPO_DIR/deploy/systemd/nginx.service.d/xblp.conf" \
   /etc/systemd/system/nginx.service.d/xblp.conf

systemctl daemon-reload
_green "  units installed and daemon reloaded"

# ── 6. Start / enable xblp-api ───────────────────────────────────────────────

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
        _red "  cert not found after 30s — check: journalctl -u xblp-api -n 50"
        exit 1
    fi
    sleep 1
done

# ── 7. Validate and start nginx ───────────────────────────────────────────────

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
