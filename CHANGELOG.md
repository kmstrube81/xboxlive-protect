# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 2, Stage 4: React UI scaffolding + auth screens

- **`ui/` directory** — Vite 5 + React 18 + TypeScript 5 SPA with Tailwind CSS 3
  and TanStack React Query v5. Build output at `ui/dist/`.
- **Vite dev server proxy** — `/api/*` forwarded to
  `https://xboxlive-protect.local` (`secure: false` for the self-signed cert).
  A `proxyRes` hook strips the `Secure` cookie flag so sessions work over
  plain `http://localhost:5173`. Override the proxy target via
  `VITE_API_TARGET=https://<ip>` in `ui/.env.local`.
- **API client** (`ui/src/api/client.ts`) — typed `fetch` wrapper; throws
  `ApiError(status, message, body)` on non-2xx; network failures surface as
  `ApiError(0, "Couldn't reach the server")`.
- **`useAuth` hook** — React Query over `GET /api/v1/auth/me`; returns
  `{user, isAuthenticated, mustChangePassword, isLoading}`.
- **`RequireAuth`** — Outlet-based v6 route guard: spinner while loading,
  redirect to `/login` when not authenticated, redirect to `/change-password`
  when `must_change_password=true` (skips the redirect when already on
  `/change-password`).
- **Login screen** — username/password form; redirects already-authenticated
  users away immediately (to `/change-password` if forced, `/` otherwise);
  displays API and network errors via `role=alert`.
- **ChangePassword screen** — three-field form (current, new, confirm);
  client-side confirmation check; navigates to `/` on success.
- **Dashboard screen** (placeholder) — fetches `GET /api/v1/status` every 10 s;
  displays version, capture status, active profile, rule count, uptime. Stage 5
  will add the Live/Rules/History screens.
- **Layout shell** — sticky top bar with logout button; footer with
  version/capture_status and GitHub link; mobile-first, max-w-4xl content area.
- **28 Vitest tests** (Windows-runnable):
  - `client.test.ts` (7): 2xx, 4xx, 5xx, network failure, 204, Content-Type header
  - `useAuth.test.tsx` (4): loading, authenticated, must_change_password, 401
  - `RequireAuth.test.tsx` (5): children/redirect/spinner cases
  - `Login.test.tsx` (7): submit happy paths, error cases, already-authenticated
    redirects
  - `ChangePassword.test.tsx` (5): success, mismatch, wrong password, network error
- **FastAPI `_SPAStaticFiles`** — `StaticFiles` subclass that catches Starlette's
  `HTTPException(404)` from `get_response` and falls back to `index.html`,
  enabling SPA client-side routing. Starlette 1.0's built-in `html=True` only
  serves `index.html` for directory paths, not arbitrary routes.
- **`/api/v1/{path:path}` catch-all** — registered before the StaticFiles mount;
  returns JSON `404` for malformed API paths instead of `index.html`, so
  curl/automation gets a debuggable error rather than `Unexpected token <`.
- **`XBLP_UI_DIST_PATH` setting** — path to the built `ui/dist/` tree (default
  `/opt/xboxlive-protect/ui/dist`). Daemon starts cleanly and logs a warning
  when the path doesn't exist; API routes are unaffected.
- **`deploy/install-stage1.sh`** — new step 2a: installs `nodejs` + `npm` via
  `apt-get` (Debian 12 ships 18.19/9.2, satisfying Vite 5's Node ≥ 18
  requirement), then runs `npm ci` and `npm run build` from `$XBLP_SRC_ROOT/ui`.
  Build failure causes the installer to exit 1 (inherited from `set -euo pipefail`).
- **7 new Python unit tests** (`test_ui_static.py`, Windows-runnable):
  static mount serves HTML, SPA fallback works, API routes not shadowed,
  `/api/v1` typo returns JSON 404, daemon starts without ui/dist.
- **`docs/ui-development.md`** — dev server setup, cookie story, proxy override,
  build output, project structure, SPA routing notes.
- **`docs/development.md`** — added Node.js to requirements, self-signed cert
  warning walkthrough (per-browser click-through steps).

### Added — Phase 2, Stage 3: capture↔API IPC + live peer endpoint with SSE

- **`PeerSnapshot` model** — one row per active peer per 1 Hz capture tick.
  Columns: `captured_at` (indexed batch timestamp), `peer_ip`, `pps` (profile
  window), `pps_5s` (fixed 5-second look-back), `score`, `flagged`,
  `bytes_in` / `bytes_out` (Xbox-relative; see below), `first_seen_at`,
  `last_seen_at`.  Created by `create_tables()` on next startup, no separate
  migration step.
- **`RuntimeState` model** — key-value store for capture daemon runtime state.
  `key='active_profile'` is written by xblp-capture at startup so the API
  daemon can read the active profile name for `GET /status`.
- **`PeerScorer` additions** (`scorer.py`):
  - `bytes_in` / `bytes_out` accumulators on `_PeerState`.  Updated in
    `observe()` using the Xbox-relative convention: packet where
    `src_ip == xbox_ip` → `bytes_out`; packet where `dst_ip == xbox_ip` →
    `bytes_in`.  "bytes_in = bytes the Xbox received" is the Live screen's
    natural frame of reference ("peer X is sending you 50 kbps").
  - `snapshot_stats(now) → list[PeerSnapshotStats]` — read-only pass over
    `_peers` returning per-peer stats for DB persistence.  Computes
    `pps_5s` from the same packet deque with a fixed 5s window.  Does not
    mutate qualification windows; call after `tick()`.
- **`flush_peer_snapshots()`** (`persistence.py`) — writes one `PeerSnapshot`
  row per peer and prunes rows older than 5 minutes (time-based window) in
  one commit.  Called at 1 Hz from the capture daemon main loop.
- **`write_active_profile()`** (`persistence.py`) — upserts
  `runtime_state[active_profile]`; called at daemon startup.
- **Capture daemon** (`__main__.py`) — evolved to full production daemon:
  - `--db-path` / `--no-db` args (`--no-db` preserves manual validation mode).
  - Opens `state.db` at startup; calls `write_active_profile()`.
  - Calls `flush_peer_snapshots()` every `_SNAPSHOT_INTERVAL` (1s) after
    `tick()`.
  - Persists detected hosts via `record_detected_host()`.
- **`xblp-capture.service`** — new systemd unit with `CAP_NET_ADMIN +
  CAP_NET_RAW`, `ProtectSystem=full`, `PrivateTmp`, `NoNewPrivileges`.
  Ordered after `network-online.target + xblp-bridge-rollback.service`.
  Independent of `xblp-api.service` (either daemon can restart without the
  other).  Runtime config via `EnvironmentFile=/etc/xboxlive-protect/capture.env`
  (`XBLP_XBOX_IP`, `XBLP_PROFILE`).
- **`install-stage1.sh` extension** — new step 9: installs capture unit, creates
  `/etc/xboxlive-protect/capture.env` with placeholder defaults if absent,
  enables and starts `xblp-capture.service`.
- **`GET /api/v1/peers`** — returns `MAX(captured_at)` batch from
  `peer_snapshots`.  200 with empty list when capture daemon has not yet run.
  Response: `{captured_at, peers: [...], count}`.
- **`GET /api/v1/peers/stream`** — Server-Sent Events at ~1 Hz.  Event format:
  `event: peers\ndata: {...}\n\n` (same JSON shape as `GET /peers`).  Fresh
  ORM session per tick (no long-held connection).  Concurrency cap: 503 when
  10 clients already connected.  `Cache-Control: no-cache`,
  `X-Accel-Buffering: no` headers.  Client disconnect handled cleanly via
  `GeneratorExit` / `CancelledError`.
- **`GET /api/v1/status`** — system health: `version`, `uptime_seconds`,
  `active_profile`, `capture_status` (`active` / `stale` / `missing` based on
  3-second threshold), `capture_last_seen`, `rules_count` (total/local/
  subscription), `blocklist_size` (live `len(nft_manager.list_blocklist())`).
- **nginx** — activated the `/api/v1/peers/stream` SSE location block (was
  stubbed since Stage 1): `proxy_buffering off`, `proxy_read_timeout 1d`.
- **`app.state` additions** — `engine`, `sse_client_count`, `start_time`
  stored in lifespan startup.
- **39 new unit tests** — PeerSnapshot round-trip, all GET /peers / GET
  /peers/stream / GET /status cases (auth gates, forced-password gate,
  capture_status variants, rules_count, response shape, SSE event format,
  503 cap).  snapshot_stats() behaviour tested in `test_scorer.py` (6 new
  tests).  All run on Windows.
- **4 integration tests** (R4S, `pytest -m integration`): DB schema accessible,
  GET /peers shape, GET /peers/stream events ≥3 over 3.5 s, GET /status shape
  with live `capture_status='active'`.
- **`docs/api-peers.md`** and **`docs/api-status.md`** — endpoint references.

### Added — Phase 2, Stage 2: Rules endpoints

- **Rules API** — five endpoints under `/api/v1/rules`:
  - `GET /rules` — list with filters (`source`, `since`, `search`) and
    cursor-free pagination (`limit`/`offset`/`total`). `source=local` returns
    user-created rules; `source=subscription` returns subscription-synced rules;
    `source=all` (default) returns both.
  - `POST /rules` — add a local block rule with IP validation (RFC1918,
    loopback, link-local, Xbox Live allowlist, gateway, bridge). Returns 422
    with a machine-readable `reason` on rejection. Returns 409 if a local rule
    for the same `(ip_address, cidr_prefix)` already exists. Calls
    `reconcile_blocklist` to project the new rule into nftables atomically.
  - `PATCH /rules/{id}` — edit `comment` and/or `confidence` on a local rule.
    True no-op detection: if the provided values match the current row, no audit
    entry is written. Returns 403 for subscription rules. `ip_address` and
    `cidr_prefix` are immutable (identity change — requires delete+recreate).
  - `DELETE /rules/{id}` — remove a local rule and reconcile nftables. Returns
    403 for subscription rules.
  - `POST /rules/{id}/promote` — promote a subscription rule to local (`source`
    → `'local'`, `subscription_id` → `NULL`). Idempotent: promoting an
    already-local rule is a 204 no-op. Returns 409 if a local rule for the same
    `(ip_address, cidr_prefix)` already exists.
- **`src/xblp_common/validation.py`** — new module: `is_ip_blockable(ip, cidr)`
  checks the IP against loopback, RFC1918, link-local, the Xbox Live allowlist,
  and the detected gateway/bridge IP. `get_default_gateway()` reads
  `/proc/net/route`; `get_bridge_ip()` uses `SIOCGIFADDR` on `br0`. Both
  return `None` on non-Linux (unit tests on Windows are safe).
- **`NoopNftManager`** — null-object `_BlocklistManager` stored on
  `app.state.nft_manager` when `XBLP_NFT_ENABLED=false`. Route handlers call
  `reconcile_blocklist(session, nft_manager)` unconditionally; no branching on
  nft_enabled in handlers.
- **`RuleList` schema** — paginated response wrapper: `total`, `items`,
  `limit`, `offset`.
- **Audit log** — every successful mutation (`POST`, `PATCH` with actual
  changes, `DELETE`, `promote`) writes a `rule_added`, `rule_edited`, or
  `rule_removed` entry with a `undo_token` (uuid4 hex). Undo logic deferred
  to Phase 4; tokens are present so Phase 4 can implement against a populated
  history.
- **75 new unit tests** covering all five endpoints, all filter combinations,
  pagination, every IP rejection reason, no-op PATCH detection, subscription
  guards, promote idempotency, and audit log entries. All run on Windows.
- **3 integration tests** (R4S, `pytest -m integration`): POST adds IP to
  `nft list set inet xblp blocklist`; DELETE removes it; audit log is correct.
- **`docs/api-rules.md`** — full endpoint reference including the uniqueness
  model (`(ip_address, cidr_prefix, source)` is the unique key — two rows for
  the same IP across sources is a feature not a bug), all error shapes, and
  `curl` examples.

### Known limitation — Stage 2

**`xbox-live` rejection is inert until Phase 3.** The `POST /rules` handler
calls `is_ip_blockable()` which checks `data/xbox-live-allowlist.json` for
Xbox Live / Microsoft IP ranges.  This file is generated by Phase 3 from
Microsoft's Azure service tag JSON and does not exist yet.  When the file is
absent, `load_xbl_allowlist()` logs a WARNING and returns an empty list — all
IPs pass the `xbox-live` check.  A real Microsoft Xbox Live IP will return 201
in Stage 2 environments.  Exit criterion #2 ("POST a real Microsoft IP returns
422 with reason 'xbox-live'") cannot be fully validated until Phase 3.  The
corresponding integration test is SKIPPED with a clear reason in
`tests/integration/test_rules_integration.py`.

---

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

[Unreleased]: https://github.com/kmstrube81/xboxlive-protect/compare/HEAD...HEAD
