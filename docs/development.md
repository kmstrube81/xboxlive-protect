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
