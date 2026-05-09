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
