# API Reference: Status

**Phase 2 Stage 3** — system health endpoint

Requires a valid session cookie (`xblp_session`) and `must_change_password=false`.

---

## GET /api/v1/status

Returns a system health snapshot including version, uptime, capture daemon state, active profile, rule counts, and blocklist size.

### Response

```json
{
  "version": "0.1.0",
  "uptime_seconds": 3847,
  "active_profile": "mw2-x360",
  "capture_status": "active",
  "capture_last_seen": "2026-05-18T12:00:01Z",
  "rules_count": {
    "total": 12,
    "local": 8,
    "subscription": 4
  },
  "blocklist_size": 12
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `version` | string | Application version from package metadata. |
| `uptime_seconds` | int | Seconds since the API daemon started. |
| `active_profile` | string or null | Profile ID currently loaded by `xblp-capture` (e.g. `"mw2-x360"`). `null` if the capture daemon has never run or the `runtime_state` table has no `active_profile` key. |
| `capture_status` | string | `"active"` / `"stale"` / `"missing"` — see below. |
| `capture_last_seen` | ISO8601 or null | Timestamp of the most recent `peer_snapshots` row. `null` if no rows exist. |
| `rules_count.total` | int | Total block rules in the database (local + subscription). |
| `rules_count.local` | int | User-created rules (`source='local'`). |
| `rules_count.subscription` | int | Rules synced from subscriptions. |
| `blocklist_size` | int | Live count of entries in the `inet xblp blocklist` nftables set. `0` on Windows dev (nftables disabled). |

### `capture_status` values

| Value | Meaning |
|---|---|
| `"active"` | `capture_last_seen` is within the last 3 seconds. The capture daemon is running and writing normally. |
| `"stale"` | `capture_last_seen` exists but is older than 3 seconds. The daemon may have stopped, crashed, or be catching up after a pause. |
| `"missing"` | No rows in `peer_snapshots`. The capture daemon has never run, or the table was wiped. |

The 3-second threshold is a system invariant: the capture daemon runs at 1 Hz, so more than 3 missed writes indicates a problem rather than transient jitter.

### Status codes

| Code | Condition |
|---|---|
| 200 | Always. |
| 401 | No valid session. |
| 403 | `must_change_password=true`. |

### curl example

```bash
curl -sk https://xboxlive-protect.local/api/v1/status \
  -H "Cookie: xblp_session=<token>" | jq .
```

### Sanity-check: blocklist_size vs nft

The `blocklist_size` field should match the output of:

```bash
nft list set inet xblp blocklist | grep -c '/'
```

Run both and compare to verify the API and nftables are in sync.
