# xboxlive-protect — Design Specification

**Version:** 0.1 (Draft)
**Status:** Pre-implementation
**Last updated:** 2026-04-28

---

## 1. Overview

`xboxlive-protect` is a transparent network bridge appliance that sits between an Xbox console and the local network, observes peer-to-peer game traffic, identifies likely lobby hosts, and lets the user manually block their IPs at the bridge level. It is built for retro Xbox Live communities where modded lobbies in older P2P-hosted games (notably Modern Warfare 2 on Xbox 360) are a persistent problem.

The project ships as both a flashable SD card image and a one-line install script for existing Debian systems. It exposes a local web UI for management, supports per-game detection profiles, and lets users import/export/subscribe to community blocklists.

### 1.1 Goals

- Run on commodity SBC hardware at low cost (target: ~$80 all-in for the reference platform)
- Zero impact on game latency under normal operation
- Zero risk of blocking legitimate Xbox Live infrastructure traffic
- Accessible to non-technical users: flash, plug in, open browser, configure
- Open source, community-maintainable game profiles and blocklists
- Compatibility with existing router/firewall blocklist ecosystems

### 1.2 Non-goals

The following are explicitly out of scope for v1.0:

- IPv6 support
- CGNAT detection or warnings
- Automatic blocking based on heuristics (all blocks are manual)
- Hardware bypass relays / fail-closed networking
- Deep packet inspection or game protocol reverse engineering
- Long-term packet capture storage
- Cryptographic signing or trust frameworks for subscriptions
- Cloud-hosted central services of any kind
- Modern game support where dedicated servers are used (no peer to identify)

---

## 2. Hardware

### 2.1 Reference hardware

The reference platform is the **FriendlyElec NanoPi R4S 4GB**. This is the only Tier 1 hardware for v1.0.

| Spec | Value |
|---|---|
| SoC | Rockchip RK3399 (2x A72 + 4x A53) |
| RAM | 4GB LPDDR4 |
| NICs | 2x native Gigabit Ethernet (one direct-attached, one PCIe) |
| Storage | microSD (no eMMC) |
| Power | USB-C, 5V/3A (no PD negotiation) |
| Form factor | 66 x 66 mm, passive aluminum case |

### 2.2 Hardware support tiers

- **Tier 1 — pre-built images, tested every release:** NanoPi R4S
- **Tier 2 — install script, known-good:** Any board contributed and validated by community members. Empty at v1.0 launch.
- **Tier 3 — install script, best-effort:** Any Debian 12+ system with two non-loopback Ethernet interfaces. Install script auto-detects interfaces and prompts the user to identify Xbox-side vs. network-side if ambiguous.

The install script's hardware detection is interface-name agnostic — it enumerates all non-loopback, non-virtual Ethernet interfaces and works with any naming scheme (`eth0`, `enp1s0`, `end0`, etc.).

---

## 3. Network Architecture

### 3.1 Topology

```
[Xbox 360] --eth--> [SBC: xbox-side NIC] --bridge--> [SBC: net-side NIC] --eth--> [Router]
```

The SBC operates as a **transparent Layer 2 bridge** (`br0`). The Xbox receives DHCP from the user's router as if the SBC weren't there. The bridge interface itself has its own IP on the LAN, obtained via DHCP, used to host the management UI and API.

### 3.2 Discovery

The device advertises itself via mDNS as `xboxlive-protect.local` (port 80 redirects to 443; UI served on 443 with self-signed cert; warning page on first connect explains the cert situation and offers a "trust permanently" walkthrough per browser).

mDNS hostname is configurable via the UI for users running multiple devices.

### 3.3 Failure mode

If the SBC powers off, crashes, or loses bridge connectivity, the Xbox loses internet (fail-open is not implemented in hardware — there's no relay). This is documented as expected behavior.

If the userspace daemon crashes but the bridge and nftables remain up, all existing block rules continue to enforce and the Xbox keeps internet. The daemon is restarted automatically by systemd.

### 3.4 Performance targets

- Sustained throughput: ≥500 Mbit/s through the bridge under normal operation (Xbox 360 caps at 100 Mbit, so this is significant headroom)
- Added latency: <1 ms p99 under normal load
- CPU usage: <30% sustained during active game traffic monitoring on R4S

---

## 4. Software Architecture

### 4.1 Stack

- **OS:** Debian 12 (Bookworm) ARM64, minimal install
- **Bridge & blocking:** Linux bridge + nftables with named sets
- **Capture:** libpcap via Python `scapy` (or `pyshark` if needed for higher throughput)
- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Database:** SQLite (`/var/lib/xboxlive-protect/state.db`)
- **Frontend:** React (Vite build), served as static assets by the FastAPI app
- **Process management:** systemd
- **Auto-update:** `unattended-upgrades` for OS, custom updater service for application

### 4.2 Components

```
┌─────────────────────────────────────────────────────────┐
│  systemd-managed services                               │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ xblp-capture │  │ xblp-api     │  │ xblp-updater │  │
│  │ (sniffer)    │  │ (FastAPI)    │  │ (cron-like)  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘  │
│         │                 │                             │
│         └────────┬────────┘                             │
│                  ▼                                      │
│         ┌────────────────┐                              │
│         │  state.db      │                              │
│         │  (SQLite)      │                              │
│         └────────────────┘                              │
│                  │                                      │
│                  ▼                                      │
│         ┌────────────────┐                              │
│         │  nftables      │                              │
│         │  set: blocklist│                              │
│         └────────────────┘                              │
└─────────────────────────────────────────────────────────┘
```

- **xblp-capture:** Sniffs the bridge, identifies the Xbox by MAC OUI, applies the active game profile to score peer traffic, writes peer-stats snapshots to SQLite at 1 Hz.
- **xblp-api:** Serves the web UI, exposes REST endpoints, manages auth, applies block/unblock actions to nftables, fetches subscriptions, exports blocklists.
- **xblp-updater:** Daily timer that checks GitHub for new releases of the application and applies updates per the policy in section 9.

### 4.3 nftables ruleset

```
table inet xblp {
    set blocklist {
        type ipv4_addr
        flags interval
    }
    set xbl_allowlist {
        type ipv4_addr
        flags interval
        elements = { /* Microsoft service tag IPs, refreshed weekly */ }
    }
    chain forward {
        type filter hook forward priority 0;
        ip saddr @xbl_allowlist accept
        ip daddr @xbl_allowlist accept
        ip saddr @blocklist drop
        ip daddr @blocklist drop
    }
}
```

The Xbox Live allowlist is checked **before** the blocklist, so it's impossible for any user action or subscription to drop traffic to/from Microsoft infrastructure.

---

## 5. Detection Model

### 5.1 Game profiles

Detection is profile-driven. Profiles are YAML files shipped in the repo at `/profiles/` and copied to `/etc/xboxlive-protect/profiles/` on install. Custom profiles drop into the same directory.

```yaml
# profiles/mw2-x360.yaml
id: mw2-x360
name: "Modern Warfare 2 (Xbox 360)"
console: xbox-360
confidence: tested
maintainer: "kasey"
last_validated: "2026-04-28"

detection:
  transport: udp
  # Match traffic where source or destination port is in this range
  port_ranges:
    - {min: 1000, max: 65535}
  # Minimum sustained packet rate to be considered a candidate host
  min_pps: 30
  # Window size in seconds for rate calculation
  window_seconds: 10
  # Minimum number of windows the IP must remain a candidate
  min_consecutive_windows: 3

# Always excluded from analysis (in addition to the global Xbox Live allowlist)
exclude_ranges: []

# Optional payload signatures (none for v1.0; reserved for future use)
payload_signatures: []
```

The active profile is selected via the UI. Available profiles at v1.0:

- **mw2-x360** — Modern Warfare 2, Xbox 360
- **halo3-x360** — Halo 3, Xbox 360 (community-validated, not maintainer-tested)
- **halo-reach-x360** — Halo Reach, Xbox 360 (community-validated)
- **generic-p2p** — Top-talker fallback for any P2P game without a custom profile
- **monitoring-only** — No detection, just shows raw peer table for debugging

### 5.2 Host scoring

Within each window, for every external IP the Xbox exchanged matching packets with:

```
score = packets_per_second * window_count_above_threshold
```

The peer table in the UI is sorted by score descending. The top peer is flagged as "likely host" with a star icon. Ties are broken by total packet count.

### 5.3 Profile constraints

Profiles can only filter *what is shown and scored* in the peer table. They cannot:

- Trigger automatic blocks
- Override the Xbox Live allowlist
- Allowlist anything (allowlists are global only)
- Execute arbitrary code (YAML-only, parsed with safe loaders)

This means a malicious or buggy community profile is at worst a nuisance (false detections), never a security issue.

### 5.4 Xbox Live allowlist sourcing

The allowlist is generated from Microsoft's published Azure service tag JSON, filtered to:

- `XboxLiveServices` tag
- `AzureCloud.*` regions used by Xbox infrastructure
- Manually curated additions for legacy 360-era IPs not in the modern service tags

Refreshed weekly by `xblp-updater`. Bundled with the install image so the device works on first boot before its first refresh.

---

## 6. Authentication

### 6.1 First-boot flow

1. SBC boots, generates a self-signed TLS cert for `xboxlive-protect.local`
2. SBC starts mDNS responder, API, and UI
3. User opens `https://xboxlive-protect.local` in a browser
4. Browser shows cert warning; user clicks through (one-time)
5. UI shows login page; user enters default credentials (`admin` / `xboxlive-protect`)
6. UI immediately redirects to a forced password change page
7. After password change, normal operation begins

### 6.2 Threat model

The shared default password is a deliberate UX trade-off, accepted with the following understanding:

- **Threat:** Another user on the LAN reaches the device first and changes the password.
- **Impact:** Annoyance only; no exfiltration of meaningful secrets.
- **Recovery:** Re-flash SD card; takes <10 minutes.

This is acceptable for the project's audience. The README will state this explicitly.

### 6.3 Session management

- Login produces a session cookie (HttpOnly, SameSite=Strict, Secure)
- Sessions last 30 days, sliding expiration
- "Log out everywhere" option in settings invalidates all sessions
- Failed login attempts are **not rate-limited and do not trigger lockout**. Given the
  threat model in §6.2 (impact is annoyance only; recovery is reflash), lockouts would
  primarily punish forgetful legitimate users. All failed attempts are recorded in the
  audit log (event type `login_failed`) so there is a complete record.
- No password recovery flow — lost password requires reflash or SSH access to reset

### 6.4 API authentication

All API endpoints require a valid session cookie. There is no API key system in v1.0; this is a single-user appliance.

---

## 7. Data Model

### 7.1 Block rules

```sql
CREATE TABLE rules (
    id INTEGER PRIMARY KEY,
    ip_address TEXT NOT NULL,           -- IPv4 only in v1.0
    cidr_prefix INTEGER DEFAULT 32,
    source TEXT NOT NULL,               -- 'local' | 'subscription:<sub_id>'
    subscription_id INTEGER,            -- FK to subscriptions, NULL for local
    comment TEXT,
    confidence TEXT,                    -- 'high' | 'medium' | 'low' | NULL
    profile_id TEXT,                    -- which profile was active when blocked
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE(ip_address, cidr_prefix, source)
);
```

A subscription rule can be "promoted" to local: the row's `source` becomes `'local'` and `subscription_id` becomes `NULL`. Promotion is idempotent; promoting an already-local rule is a no-op.

### 7.2 Subscriptions

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL,               -- 'json' | 'txt'
    enabled BOOLEAN DEFAULT 1,
    refresh_interval_hours INTEGER DEFAULT 24,
    last_fetched_at TIMESTAMP,
    last_success_at TIMESTAMP,
    last_error TEXT,
    rule_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);
```

Unsubscribing deletes the subscription and cascades to delete all rules with that `subscription_id`. Promoted rules are retained because they no longer reference the subscription.

### 7.3 Audit log

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,           -- 'rule_added' | 'rule_removed' | 'rule_edited'
                                        -- 'subscription_added' | 'subscription_removed'
                                        -- 'subscription_synced' | 'profile_changed'
                                        -- 'host_detected' | 'login' | 'login_failed'
                                        -- 'password_changed' | 'config_changed'
    actor TEXT,                         -- 'user' | 'system' | 'subscription:<id>'
    target TEXT,                        -- IP, sub ID, etc.
    details JSON,                       -- event-specific structured data
    undo_token TEXT                     -- present on user actions for undo stack
);
```

### 7.4 Detection log

```sql
CREATE TABLE detected_hosts (
    id INTEGER PRIMARY KEY,
    detected_at TIMESTAMP NOT NULL,
    ip_address TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    score REAL NOT NULL,
    duration_seconds INTEGER NOT NULL,
    asn TEXT,
    country_code TEXT,
    was_blocked BOOLEAN DEFAULT 0
);
```

Retention: 30 days, then automatically pruned. No pcaps stored.

### 7.5 Geolocation and ASN

Peer geolocation and ASN are looked up from a local MaxMind GeoLite2 database (Country + ASN editions, free tier). The database is bundled with the image and refreshed monthly by `xblp-updater`.

Looked-up data is cached per-IP in memory for the duration of a session and shown in the live peer table and detection log.

---

## 8. Blocklist Formats

### 8.1 Canonical JSON format

```json
{
  "format_version": "1.0",
  "name": "Kasey's MW2 Modder List",
  "description": "Hosts I've encountered running modded MW2 lobbies",
  "maintainer": "kasey",
  "homepage": "https://github.com/kasey/mw2-blocklist",
  "license": "CC0-1.0",
  "updated_at": "2026-04-28T12:00:00Z",
  "version": 47,
  "default_confidence": "medium",
  "entries": [
    {
      "ip": "203.0.113.45",
      "cidr": 32,
      "comment": "spinbot host, MW2 free-for-all",
      "confidence": "high",
      "added_at": "2026-04-15T22:30:00Z",
      "profile": "mw2-x360"
    }
  ]
}
```

All fields except `format_version`, `name`, `entries`, and `entries[].ip` are optional. Unknown fields are preserved on round-trip.

### 8.2 Plain text format (router-compatible)

A `.txt` sibling is auto-generated from the JSON when exporting or publishing:

```
# Kasey's MW2 Modder List
# Updated: 2026-04-28T12:00:00Z
# Version: 47
# Source: https://github.com/kasey/mw2-blocklist
203.0.113.45
198.51.100.0/24
```

This format imports directly into:

- pfSense / OPNsense (URL Table Aliases)
- OpenWrt banIP package
- AdGuard Home / Pi-hole-style blockers
- Most consumer router URL-based blocklist features

When a user publishes a list, both files are written to the same directory; subscribers can choose which to use based on their tooling.

### 8.3 Sanity-check filtering

Subscriptions and imports are filtered through a hardcoded reject list before being applied:

- RFC 1918 private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Loopback (127.0.0.0/8)
- Link-local (169.254.0.0/16)
- The current Xbox Live allowlist
- The user's own gateway IP and bridge IP

Rejected entries are logged and surfaced in the UI as "filtered" with the reason. The subscription is not failed — valid entries still apply.

---

## 9. Updates

### 9.1 OS updates

`unattended-upgrades` is configured to install security updates from `bookworm-security` automatically, daily, with automatic reboot disabled. Users see a "reboot needed" indicator in the UI when relevant.

### 9.2 Application updates

`xblp-updater` runs daily at 04:00 local time and checks the GitHub Releases API for new releases of `xboxlive-protect`.

Update policy:

- **Patch versions** (1.0.x): Auto-applied, service restarted, user notified post-fact in UI
- **Minor versions** (1.x.0): Notification banner in UI, user clicks "update" to apply
- **Major versions** (x.0.0): Notification banner with changelog summary, user must read and click "update", may require reboot

Updates are atomic: download to a staging directory, verify checksum from the release manifest, swap symlinks, restart services. Failed updates roll back automatically.

### 9.3 Profile and allowlist updates

- Game profiles update with the application (shipped in releases)
- Xbox Live allowlist refreshes weekly from Microsoft's service tag JSON
- GeoIP database refreshes monthly

All three can be manually refreshed from the UI's settings page.

---

## 10. API Surface

All endpoints are under `/api/v1/`. Authentication via session cookie.

### 10.1 Status

- `GET /status` — system status, active profile, Xbox detection state
- `GET /peers` — current peer table for active profile
- `GET /peers/stream` — Server-Sent Events stream of peer updates (1 Hz)

### 10.2 Rules

- `GET /rules` — list rules with filters: `source`, `since`, `search`
- `POST /rules` — add local rule `{ip, cidr, comment, confidence}`
- `PATCH /rules/{id}` — edit local rule (subscription rules return 403)
- `DELETE /rules/{id}` — remove local rule (subscription rules return 403)
- `POST /rules/{id}/promote` — promote subscription rule to local
- `POST /rules/import` — import JSON or TXT file as local rules

### 10.3 Undo

- `GET /undo/stack` — list of undoable actions in the last 30 minutes
- `POST /undo/{token}` — undo a specific action
- `POST /undo/last` — undo most recent action

### 10.4 Subscriptions

- `GET /subscriptions` — list all
- `POST /subscriptions` — add `{name, url, refresh_interval_hours}`
- `DELETE /subscriptions/{id}` — unsubscribe (cascades rules)
- `POST /subscriptions/{id}/refresh` — manual refresh

### 10.5 Profiles

- `GET /profiles` — list available profiles with metadata
- `GET /profiles/active` — currently active profile
- `POST /profiles/active` — set active profile `{id}`

### 10.6 Export

- `GET /export/json` — full local rules as canonical JSON
- `GET /export/txt` — full local rules as plain text
- `GET /export/all.json` — local + all subscription rules merged

### 10.7 Detection log

- `GET /detections` — recent detections with filters: `since`, `profile`, `blocked`

### 10.8 System

- `GET /system/info` — version, uptime, hardware, hostname
- `POST /system/restart` — restart application services
- `POST /system/reboot` — reboot the device
- `POST /auth/login` — login
- `POST /auth/logout` — logout
- `POST /auth/password` — change password

---

## 11. UI

### 11.1 Screens

1. **Live** (default) — Active profile selector at top; live peer table with columns: IP, ASN, Country (flag), Packets/s, Score, Star (likely host), [Block] button. Updates via SSE.
2. **Rules** — Sortable, filterable table of all rules. Columns: IP, Source (Local/<Sub Name>), Comment, Added, [Edit/Delete/Promote] buttons. Search box. Tabs: All / Local / By Subscription.
3. **History** — Recent actions with undo buttons. Time range selector.
4. **Detections** — Detection log with timestamps, IPs, scores, geolocation, whether blocked.
5. **Subscriptions** — List of subscriptions with status, last sync, rule count, refresh/remove buttons. Add new subscription form.
6. **Profiles** — Profile selector with descriptions, confidence badges, last validated dates.
7. **Settings** — Hostname, password change, update settings, allowlist refresh, GeoIP refresh, export buttons.

### 11.2 Always-visible elements

- Top bar: device hostname, active profile, Xbox connection status, undo button (last action), notification badge
- Footer: version, uptime, link to GitHub repo

### 11.3 Mobile responsiveness

UI must be usable on a phone, since the most common use case is "I notice a modded lobby on my couch and want to block from my phone." Single-column layouts at <768px; the live peer table collapses to cards.

---

## 12. Logging and Privacy

### 12.1 What is logged

- All user actions (rule changes, subscription changes, logins, profile changes)
- All host detections (with IP, ASN, country, score)
- All subscription syncs (success/failure, rule deltas)
- System events (reboot, update applied, services restarted)

### 12.2 What is NOT logged

- Packet contents (no DPI is done in v1.0)
- Full pcaps (only in-memory rolling window during analysis)
- Browsing or non-game traffic from the Xbox

### 12.3 Retention

- Audit log: 90 days, then pruned
- Detection log: 30 days, then pruned
- Subscription sync log: 30 days

### 12.4 Export

The settings page has a "Download diagnostic bundle" button that exports logs as a zip for sharing in bug reports. IPs are *not* redacted by default (they're not PII in this context — they're the hosts the user has been gaming with) but the user is warned before download.

---

## 13. Repository Structure

```
xboxlive-protect/
├── README.md
├── LICENSE                     (GPL-3.0)
├── DESIGN.md                   (this document)
├── CONTRIBUTING.md
├── CHANGELOG.md
├── .github/
│   ├── workflows/
│   │   ├── ci.yml             (lint, test, build)
│   │   ├── release.yml        (build & publish images, releases)
│   │   └── allowlist.yml      (weekly Xbox Live allowlist refresh PR)
│   └── ISSUE_TEMPLATE/
│       ├── bug-report.md
│       ├── profile-submission.md
│       └── feature-request.md
├── src/
│   ├── xblp_capture/          (Python: packet sniffer + scorer)
│   ├── xblp_api/              (Python: FastAPI app)
│   ├── xblp_updater/          (Python: update daemon)
│   └── xblp_common/           (shared models, db access)
├── ui/
│   ├── src/                   (React + Vite)
│   ├── public/
│   └── package.json
├── profiles/
│   ├── mw2-x360.yaml
│   ├── halo3-x360.yaml
│   ├── halo-reach-x360.yaml
│   ├── generic-p2p.yaml
│   └── monitoring-only.yaml
├── data/
│   ├── xbox-live-allowlist.json   (refreshed weekly by CI)
│   └── geolite2/                  (downloaded at install, not committed)
├── deploy/
│   ├── systemd/
│   │   ├── xblp-capture.service
│   │   ├── xblp-api.service
│   │   └── xblp-updater.service
│   ├── nftables/
│   │   └── xblp.nft
│   ├── network/
│   │   └── br0.conf
│   └── install.sh             (one-line installer for existing Debian)
├── image-builder/
│   ├── Dockerfile
│   ├── build-r4s.sh           (builds the R4S SD image)
│   └── README.md
├── docs/
│   ├── installation.md
│   ├── first-time-setup.md
│   ├── creating-profiles.md
│   ├── publishing-blocklists.md
│   └── troubleshooting.md
└── tests/
    ├── unit/
    ├── integration/
    └── pcap-fixtures/         (sample captures for profile testing)
```

---

## 14. Install Flows

### 14.1 SD card image (Tier 1)

1. User downloads `xboxlive-protect-v1.0.0-r4s-arm64.img.xz` from GitHub Releases
2. Verifies sha256 against published checksum
3. Flashes to microSD with Raspberry Pi Imager, balenaEtcher, or `dd`
4. Inserts SD into R4S, plugs Xbox into the inner port, plugs network into the outer port, plugs in USB-C power
5. Waits ~90 seconds for first boot
6. From any device on the LAN, browses to `https://xboxlive-protect.local`
7. Accepts cert warning, logs in with default credentials, changes password
8. Done

### 14.2 Install script (Tier 2/3)

```bash
curl -sSL https://raw.githubusercontent.com/<org>/xboxlive-protect/main/deploy/install.sh | sudo bash
```

The script:

1. Verifies Debian 12+ (or compatible: Ubuntu 22.04+, Raspberry Pi OS Bookworm)
2. Detects Ethernet interfaces; prompts user to identify Xbox-side and network-side if more than two
3. Installs system dependencies (nftables, avahi-daemon, python3-venv, etc.)
4. Creates `xblp` user, `/etc/xboxlive-protect/`, `/var/lib/xboxlive-protect/`
5. Downloads latest release bundle, extracts to `/opt/xboxlive-protect/`
6. Configures bridge in `/etc/network/interfaces.d/`
7. Installs nftables ruleset
8. Installs systemd units, enables and starts services
9. Prints the URL to access the UI and the default credentials

The script supports `--unattended` for CI, `--manual-interfaces` to skip auto-detection, `--no-bridge` for users who want to set up networking themselves.

---

## 15. Phased Implementation Plan

### Phase 1 — Bridge and capture (weeks 1-2)

- Bridge configuration on R4S
- nftables ruleset with allowlist + blocklist sets
- `xblp-capture` daemon: sniff bridge, parse UDP, calculate per-peer pps
- SQLite schema and writes
- Unit tests with pcap fixtures

**Exit criteria:** Bridge passes traffic at >500 Mbit/s, capture daemon logs detected hosts to DB during a real MW2 session.

### Phase 2 — API and basic UI (weeks 3-4)

- FastAPI scaffolding with auth
- Rules endpoints
- Live peer endpoint with SSE
- Minimal React UI: Live, Rules, History, Settings screens
- Login flow with forced password change

**Exit criteria:** Can manually block an IP from the web UI on a phone during a live game session and the rule applies in nftables.

### Phase 3 — Profiles and detection (weeks 5-6)

- Profile YAML schema and loader
- Profile-aware filtering in capture daemon
- MW2, Halo 3, Halo Reach, generic, monitoring profiles
- Xbox Live allowlist integration
- GeoIP and ASN lookups in peer table
- Detection log

**Exit criteria:** Active profile changes what's shown in the UI; Microsoft IPs are never blockable; detection log accurately records hosts with geolocation.

### Phase 4 — Blocklists and subscriptions (weeks 7-8)

- JSON and TXT export
- Import flow
- Subscription model (add, sync, unsubscribe)
- Sanity-check filtering
- Promote-to-local action
- Audit log and undo stack

**Exit criteria:** Can subscribe to a remote URL, see imported rules attributed to the subscription, promote one to local, unsubscribe and watch the others disappear.

### Phase 5 — Updates and polish (weeks 9-10)

- `unattended-upgrades` config
- `xblp-updater` service
- Allowlist and GeoIP refresh jobs
- Mobile responsive polish
- Documentation
- Image build pipeline for R4S

**Exit criteria:** A clean SD flash on R4S yields a working device that auto-updates; documentation is sufficient for a new user with no prior knowledge.

### Phase 6 — Release (week 11)

- Beta testing with community
- Bug fixes
- v1.0.0 release

---

## 16. Open Questions

These should be resolved before or during implementation:

1. **Profile validation flow.** When a community member submits a new game profile via PR, how is it validated before merging? Proposal: PR must include a pcap fixture demonstrating detection working, plus the maintainer running it for at least one session and reporting in the PR. CI runs the profile against the fixture as a regression test.

2. **Subscription discovery.** Where do users find lists to subscribe to? Proposal for v1.0: a `community-blocklists.md` page in the repo where people PR their list URLs. Out of scope: a centralized directory.

3. **What happens when the user's router IP changes** (via DHCP renewal)? The bridge IP is dynamic; this should be transparent. Need to verify mDNS responder handles this gracefully.

4. **Multiple Xboxes on the same network.** Out of scope for v1.0 (single-Xbox appliance), but worth noting as a future enhancement so the data model doesn't preclude it.

5. **Console identification.** v1.0 identifies the Xbox by being the only thing on the inner port. A future "monitor existing LAN traffic" mode would need explicit MAC selection. Not in v1.0 but the capture daemon should be written to make this easy to add.

---

## 17. License and Project Governance

- **License:** GPL-3.0 (chosen so that derivatives must remain open)
- **Game profiles:** CC0-1.0 (public domain dedication, encourages contribution)
- **Blocklists published by users:** Contributors choose; CC0 recommended in template
- **Project hosting:** GitHub
- **Issue triage:** maintainer-driven for v1.0; expand to community moderators if project grows

---

## Appendix A — Glossary

- **Bridge:** Layer 2 network device that forwards frames between two physical interfaces transparently. The Xbox sees the network as if directly connected.
- **Profile:** A YAML configuration that defines what game traffic looks like for detection purposes.
- **Local rule:** A block rule the user added directly. Editable and deletable.
- **Subscription rule:** A block rule synced from a remote subscription URL. Read-only until promoted.
- **Promote:** Convert a subscription rule into a local rule, detaching it from the subscription.
- **Allowlist:** The hardcoded set of Xbox Live infrastructure IPs that can never be blocked.
- **Top-talker:** The peer IP exchanging the most packets per second with the Xbox; the most likely host.

---

## Appendix B — Sample Profile YAML

See section 5.1 for the full schema. Minimal example for community submissions:

```yaml
id: gow2-x360
name: "Gears of War 2 (Xbox 360)"
console: xbox-360
confidence: experimental
maintainer: "your-github-handle"
last_validated: "2026-04-28"
detection:
  transport: udp
  port_ranges:
    - {min: 1000, max: 65535}
  min_pps: 25
  window_seconds: 10
  min_consecutive_windows: 3
exclude_ranges: []
```

---

*End of design specification.*
