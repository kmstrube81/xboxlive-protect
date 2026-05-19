# API Reference: Peers

**Phase 2 Stage 3** — capture↔API IPC + live peer endpoint

All endpoints require a valid session cookie (`xblp_session`) and `must_change_password=false`.

---

## GET /api/v1/peers

Returns the most recent snapshot of all active peers observed by the capture daemon.

### Response

```json
{
  "captured_at": "2026-05-18T12:00:01Z",
  "peers": [
    {
      "peer_ip": "203.0.113.45",
      "pps": 35.2,
      "pps_5s": 33.1,
      "score": 105.6,
      "flagged": true,
      "bytes_in": 184320,
      "bytes_out": 92160,
      "first_seen_at": "2026-05-18T11:59:45Z",
      "last_seen_at": "2026-05-18T12:00:01Z"
    }
  ],
  "count": 1
}
```

| Field | Type | Description |
|---|---|---|
| `captured_at` | ISO8601 or null | Batch timestamp — when the capture daemon wrote this batch. `null` if no snapshots exist yet. |
| `peers` | array | One object per active peer in the most recent capture tick. |
| `count` | int | Length of `peers`. |

**Peer object fields:**

| Field | Type | Description |
|---|---|---|
| `peer_ip` | string | Peer IPv4 address. |
| `pps` | float | Packets per second over the active profile's detection window (typically 10 s). |
| `pps_5s` | float | Packets per second over a fixed 5-second look-back window (profile-independent). |
| `score` | float | `pps × qualified_windows` — the composite detection metric. |
| `flagged` | bool | `true` when `pps >= min_pps` AND `qualified_windows >= min_consecutive_windows`. |
| `bytes_in` | int | Session-total bytes the **Xbox received** from this peer. |
| `bytes_out` | int | Session-total bytes the **Xbox sent** to this peer. |
| `first_seen_at` | ISO8601 | Timestamp of the first packet observed from/to this peer this session. |
| `last_seen_at` | ISO8601 | Timestamp of the most recent packet. |

**Bytes direction convention**: `bytes_in` and `bytes_out` are Xbox-relative. If the peer is sending you 50 kbps, that traffic is `bytes_in`. The Live screen displays "peer X is sending you N kbps."

### Status codes

| Code | Condition |
|---|---|
| 200 | Always (including when peers list is empty). |
| 401 | No valid session. |
| 403 | `must_change_password=true`. |

### curl example

```bash
curl -sk https://xboxlive-protect.local/api/v1/peers \
  -H "Cookie: xblp_session=<token>" | jq .
```

---

## GET /api/v1/peers/stream

Server-Sent Events stream of peer updates at approximately 1 Hz.

### Event format

```
event: peers
data: {"captured_at": "2026-05-18T12:00:01Z", "peers": [...], "count": 1}

```

Each event carries the same JSON shape as `GET /peers`. One event is emitted per second. If no peers are active, the event still fires with an empty `peers` array.

### Response headers

| Header | Value |
|---|---|
| `Content-Type` | `text/event-stream` |
| `Cache-Control` | `no-cache` |
| `X-Accel-Buffering` | `no` (prevents nginx buffering) |

### Concurrency limit

A maximum of 10 concurrent SSE connections are allowed. The 11th connection receives `503 Service Unavailable`:

```json
{
  "detail": {
    "error": "too_many_sse_clients",
    "message": "Maximum 10 concurrent SSE clients reached."
  }
}
```

### Status codes

| Code | Condition |
|---|---|
| 200 | Stream opened successfully. |
| 401 | No valid session. |
| 403 | `must_change_password=true`. |
| 503 | 10 SSE clients already connected. |

### curl example

```bash
# -N: disable buffering so events appear as they arrive
curl -skN https://xboxlive-protect.local/api/v1/peers/stream \
  -H "Cookie: xblp_session=<token>"
```

Expected output:
```
event: peers
data: {"captured_at": "2026-05-18T12:00:01Z", "peers": [], "count": 0}

event: peers
data: {"captured_at": "2026-05-18T12:00:02Z", "peers": [], "count": 0}
```

### Disconnect behaviour

When the client disconnects (Ctrl-C on curl, browser tab closed), the server-side generator exits cleanly via `GeneratorExit` and decrements the connection counter. No daemon state leaks.

---

## Architecture notes

The capture daemon (`xblp-capture.service`) writes peer snapshots to `peer_snapshots` in `state.db` at 1 Hz. The API daemon reads the latest batch by querying `MAX(captured_at)`. The two daemons share the SQLite database via WAL mode — either can restart independently without affecting the other. Snapshots are pruned to a 5-minute rolling window on each write.
