# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 2, Stage 1: API daemon scaffolding, auth, and TLS

- **API daemon** (`src/xblp_api/`) — FastAPI app served by Uvicorn under systemd.
  Lifespan startup runs migrations, installs the `inet xblp` nftables ruleset
  if absent (closing Phase 1's sharp-edge #3), and seeds a default `admin`
  user on a fresh database.
- **Auth model** — argon2id password hashing, server-side sessions stored in
  SQLite, HttpOnly + Secure + SameSite=Strict session cookies, 30-day sliding
  expiration. Forced password change for the seeded admin: every endpoint
  except `/auth/password`, `/auth/logout`, and `/auth/me` returns 403 until
  the default password is rotated.
- **Auth endpoints** — `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`,
  `POST /api/v1/auth/password`, `GET /api/v1/auth/me`. Every successful and
  failed authentication writes to `audit_log`.
- **New `audit_log` event types** — `password_changed` and `logout` (not
  enumerated in DESIGN.md §7.3 originally; added here).
- **TLS** — self-signed RSA 2048 cert with CN `xboxlive-protect` and SAN
  containing `xboxlive-protect.local` + `xboxlive-protect`, auto-generated
  at first daemon start, stored at `/var/lib/xboxlive-protect/{cert,key}.pem`.
  10-year validity, idempotent regeneration check.
- **nginx reverse-proxy** — terminates TLS on 443, redirects 80→443, proxies
  to the Uvicorn loopback listener. Config at `deploy/nginx/xblp.conf`,
  installed by `deploy/install-stage1.sh`. SSE-ready location block stubbed
  for Phase 2 Stage 3.
- **Uvicorn loopback bind** — daemon now binds `127.0.0.1:8080` by default
  (was `0.0.0.0:8080`). Only nginx is externally reachable. `XBLP_BIND_HOST`
  env override available for Windows dev.
- **systemd hardening** — `CAP_NET_ADMIN` granted via `AmbientCapabilities`,
  `NoNewPrivileges`, `ProtectSystem=full`, `ReadWritePaths`, `PrivateTmp`,
  `StateDirectory=xboxlive-protect`. `ProtectHome=true` deliberately omitted
  for the Stage 1 dev install layout; Phase 5 will reinstate when code lives
  at `/opt/xboxlive-protect/` for real. `systemd-analyze security` exposure
  score: 6.1 (MEDIUM).
- **Startup probes** — daemon refuses to start if the state directory or
  `state.db` is not writable by the service user. Clear error message in
  journalctl includes current user, DB path, and `chown` hint. Closes a
  common dev pitfall (running the daemon as root for testing leaves
  root-owned files that the `xblp` user later can't write).
- **`deploy/install-stage1.sh`** — idempotent installer for the dev R4S
  deployment story. Creates the `xblp` service user, ensures
  `/var/lib/xboxlive-protect/` with correct ownership, symlinks
  `/opt/xboxlive-protect/` to the dev checkout (so the systemd unit's
  production path resolves), installs nginx + systemd units, waits for cert
  generation, validates the nginx config. Fails loudly with journalctl dumps
  on any step that times out. Phase 5's image builder will replace the
  symlink step with a real `/opt/xboxlive-protect/` install.
- **`cryptography>=42`** added as a runtime dependency (used for cert
  generation).
- **`httpx>=0.27`** added to the `dev` optional dependency set (used for
  FastAPI test clients).
- **Test counts** — 33 new auth unit tests, 7 new integration tests covering
  cert generation, nginx config syntax, the full HTTPS chain via curl, the
  HTTP→HTTPS redirect, the writability probe, and the CAP_NET_ADMIN nft
  bootstrap via systemctl. Total: 200+ unit tests, 46 integration tests
  passing on R4S.
- **Documentation** — `docs/api-auth.md` (auth model reference),
  `docs/development.md` (dev DB setup, TLS overview, Windows dev notes,
  R4S deploy instructions, warnings about root-owned state files).

### Changed

- **DESIGN.md §6.3** — removed the 5-per-minute login rate-limit / 5-minute
  lockout. The threat model is annoyance-tier (§6.2) and lockouts mostly
  punish forgetful legitimate users. Failed login attempts are still
  recorded in `audit_log`.

### Phase 1 (previously, summary)

Phase 1 delivered:

- Data layer: SQLAlchemy models, SQLite schema, migrations, Pydantic schemas.
- Profile system: YAML loader with MW2 / Halo 3 / Halo Reach / generic-p2p /
  monitoring-only profiles.
- nftables: `NftManager` + reconciler, atomic transactions, DB as source of
  truth, nft state as projection.
- Capture daemon: scapy sniffer on a named interface, `PeerScorer` with
  rolling-window pps-based scoring, CLI entrypoint.
- L2 bridge on R4S: `br0` with `eth0` (WAN) and `eth1` (LAN/Xbox) as slave
  ports, `br_netfilter` loaded, `bridge-nf-call-iptables=1` so nftables
  forward chain filters bridged traffic.

Phase 1 test counts at handoff: 120 unit tests, 19 integration tests passing.

[Unreleased]: https://github.com/kstrube/xboxlive-protect/compare/HEAD...HEAD
