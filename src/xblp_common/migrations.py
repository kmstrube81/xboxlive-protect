"""Database schema creation (see DESIGN.md §4.1).

v1 uses create_all for simplicity. Alembic will be layered on top if schema
migrations are needed in a future release.
"""

from sqlalchemy import Engine

from xblp_common.models import Base


def create_tables(engine: Engine) -> None:
    """Create all tables that do not yet exist. Safe to call on every startup."""
    Base.metadata.create_all(engine)
