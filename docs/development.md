# Development Guide

## Requirements

- Python 3.11 or newer (3.11 is the minimum; match what ships on Debian 12 Bookworm)
- A POSIX-like shell for the `make` targets; on Windows use WSL or run commands directly

## Setup

```bash
# Install the package and all dev dependencies in editable mode
pip install -e ".[dev]"
```

This installs `pytest`, `ruff`, `mypy`, and stubs into the active environment alongside the production dependencies (`pydantic`, `sqlalchemy`, `pyyaml`, `structlog`).

## Running checks

```bash
# Tests
pytest

# Linting and formatting checks
ruff check .
ruff format --check .

# Type checking
mypy src/
```

All three must pass before opening a PR. CI enforces the same commands.

## Development database

The runtime database path is controlled by the `XBLP_DB_PATH` environment variable (see `src/xblp_common/db.py`). The production default is `/var/lib/xboxlive-protect/state.db`, which does not exist on a dev machine.

For local development, point it at a scratch file:

```bash
export XBLP_DB_PATH=./dev.db
```

The file is created automatically on first run. Add `dev.db` to your local `.git/info/exclude` if you do not want to commit it. The repo's `.gitignore` does not track it.

**Warning — root-owned DB pitfall:** If you run the daemon manually as root for testing (`sudo python -m xblp_api`), the DB will be created with root ownership and the systemd-managed daemon (running as `xblp`) will later fail to write to it. The daemon now detects this at startup and exits with a clear error, but the fix is still manual. Either run the daemon as the `xblp` user (`sudo -u xblp python -m xblp_api`), use a local path (`XBLP_DB_PATH=./dev.db`) so the production state dir is not affected, or re-run `deploy/install-stage1.sh` — its `chown -R` step will restore correct ownership.

## Running integration tests

Integration tests exercise real system calls against kernel subsystems. They
are automatically skipped on non-Linux platforms, when the required binary is
absent, or when not running as root.

```bash
# Integration tests require Linux + root + the nft binary
sudo pytest -m integration -v
```

To run both unit and integration tests in one pass (on Linux as root):

```bash
sudo pytest -m "unit or integration" -v
```

The integration test suite uses the table name `xblp_test` instead of `xblp`,
so it is safe to run on a machine with a live production install — the two
tables are completely independent.

**Do not** run `pytest -m integration` on a system where you cannot afford
even transient nftables state, since the tests create and destroy a real
kernel table.

## Working on Linux-specific code from Windows

Some modules (`nft.py`, future network code) wrap Linux-only tools. The
development pattern is:

1. **Write and unit-test on Windows.** Unit tests mock all subprocess calls
   and run fully cross-platform. This is the fast inner loop.

2. **Push to a branch:**
   ```bash
   git push origin <branch-name>
   ```

3. **Pull and run integration tests on the R4S (or any Debian 12 host):**
   ```bash
   git pull
   pip install -e ".[dev]"
   sudo pytest -m integration -v
   ```

4. **Fix issues found on hardware, commit, push back.**

Aim to make unit tests thorough enough that integration test failures are
surprises rather than first-time discoveries. The structural invariant tests
in `test_nft.py` (allowlist ordering, set flags, chain priority) are
specifically designed to catch template regressions before they reach
hardware testing.

## Capture daemon manual testing

`python -m xblp_capture` is a development tool that wires `live_capture` →
`PeerScorer` and prints results without touching the database.  Use it during
a real MW2 session on the R4S to validate detection before wiring up the full
daemon.

**Requires:** Linux, root (or `CAP_NET_RAW`), and the package installed in
an editable virtualenv.

```bash
# On the R4S, with the Xbox already on the network:
sudo .venv/bin/python -m xblp_capture \
    --interface br0 \
    --xbox-ip 192.168.1.50 \
    --profile mw2-x360
```

What you see:

- **stdout** — `[DETECTED]` lines whenever a peer crosses the detection
  threshold for the first time.  Each line shows the peer IP, score, and
  how long it has been qualifying.
- **stderr** — a peer table every 5 seconds showing all observed peers,
  their current packets/s, score, and qualified-window count.

Flags:

| Flag | Description |
|---|---|
| `--interface` | Bridge or NIC to sniff (e.g. `br0`, `eth0`) |
| `--xbox-ip` | Xbox IP address on the LAN |
| `--profile` | Profile ID (e.g. `mw2-x360`) or path to a YAML file |
| `--profiles-dir` | Override the profiles directory (default: repo `profiles/`) |

Stop with `Ctrl-C` — the sniffer shuts down cleanly.

**What this does NOT do:** write to the database, apply blocks, or run as
a long-lived daemon.  Those come in later stages.  This is purely for
eyeballing whether the scorer sees the right peers during a live game.

## Development TLS

The API daemon generates a self-signed TLS certificate at first startup on Linux. The cert is written to `/var/lib/xboxlive-protect/cert.pem` (and `key.pem`) during the lifespan startup sequence. Generation is idempotent — if both files already exist they are not touched.

**Certificate details:**

| Field     | Value |
|-----------|-------|
| CN        | `xboxlive-protect` |
| SAN       | `DNS:xboxlive-protect.local`, `DNS:xboxlive-protect` |
| Key       | RSA-2048 |
| Validity  | 10 years (no rotation — LAN appliance, see DESIGN.md §6.1) |

**Trusting the cert from a dev machine:**

- One-off curl: `curl -k https://xboxlive-protect.local/api/v1/auth/me`
- Browser / permanent trust: copy `/var/lib/xboxlive-protect/cert.pem` to your machine and import it as a trusted certificate authority. The exact steps vary by browser; search "import CA certificate <browser name>".

**Windows-side dev runs without TLS.** `tls_enabled` defaults to `False` on non-Linux hosts, so the daemon never generates a cert. Port 8080 is now loopback-only in production — LAN access goes through nginx on 443. On Windows you access the API directly:

```
XBLP_BIND_HOST=0.0.0.0   # only needed if testing from another machine on the LAN
XBLP_COOKIE_SECURE=false
XBLP_NFT_ENABLED=false
XBLP_DB_PATH=state.db
```

Then `curl http://localhost:8080/api/v1/auth/me`.

> **Note for anyone remembering the Stage 1 curl-on-8080 flow:** port 8080 is loopback-only from Stage 1 TLS onward. On the R4S, `curl http://localhost:8080/...` still works (from the device itself), but LAN access must go through nginx on 443. Direct LAN connections to port 8080 will be refused.

## Deploying to R4S

After installing the Python package at `/opt/xboxlive-protect`, run the system installer:

```bash
sudo bash deploy/install-stage1.sh
```

This script:
- Installs nginx via apt
- Creates the `xblp` service user
- Installs the nginx reverse-proxy config and enables it
- Installs systemd units including the nginx ordering drop-in
- Starts `xblp-api`, waits for cert generation, then starts nginx

The script is idempotent — safe to re-run on an already-installed system. See `deploy/install-stage1.sh` for the full step list; it is the authoritative spec for the Phase 5 SD image builder.

After the script completes, open `https://xboxlive-protect.local` in a browser, accept the self-signed cert warning (one-time), and log in with the default credentials (`admin` / `xboxlive-protect`). You will be required to change the password immediately.

## Gotchas

### SQLite foreign key enforcement requires PRAGMA foreign_keys=ON

SQLite does **not** enforce foreign key constraints by default. The application relies on `ON DELETE CASCADE` from `rules.subscription_id → subscriptions.id`, and this cascade will silently not fire unless the pragma is enabled on every connection.

`db.create_engine()` registers a SQLAlchemy `connect` event listener that issues `PRAGMA foreign_keys=ON` (and `PRAGMA journal_mode=WAL`) on every new connection. **Any code that creates a SQLAlchemy engine without going through `db.create_engine()`** — including test fixtures that call `sqlalchemy.create_engine` directly — must register the same listener manually, or cascade deletes will not work and tests will give false positives.

The project's `tests/conftest.py` demonstrates the correct pattern for in-memory test engines:

```python
from sqlalchemy import event

def _enable_foreign_keys(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

event.listen(engine, "connect", _enable_foreign_keys)
```

Do not bypass `db.create_engine()` in application code. Do not omit the listener in test fixtures that test cascade behaviour.

### nftables blocklist state is owned by `reconcile_blocklist`

`NftManager.add_to_blocklist` and `remove_from_blocklist` are public methods, but they should be treated as private to `xblp_common.reconcile`.  Only `reconcile_blocklist` should call them.

The reconciler diffs DB rules against live nft state and applies the minimal delta.  If other code calls the add/remove methods directly, it will fight with the diff and produce state thrash — entries may be re-added or removed on the next reconcile in ways that don't match the DB.

`reconcile_blocklist` also collapses overlapping DB entries via `_collapse_entries` before diffing.  This is required because nftables sets with `flags interval` reject overlapping CIDRs at the kernel level.  A subscription that covers a /24 will absorb any /32 entries inside it, collapsing them to a single kernel entry.  When that subscription is removed, the next reconcile will detect the difference and restore the narrower entry automatically — but only if all blocklist writes go through the reconciler.

### `_collapse_entries` normalises host-bit-set addresses

`ipaddress.IPv4Network` is called with `strict=False` inside `_collapse_entries`, which normalises host-bit-set addresses like `1.2.3.4/24` to their network address `1.2.3.0/24` before collapsing.  This means the `(ip, cidr)` tuples stored in `ReconcileResult.added` and returned by `list_blocklist` may differ from the raw strings in the DB if the DB contains un-normalised entries.
