"""baseline initial schema

Creates the COMPLETE J.A.R.V.I.S. schema from the canonical SQLAlchemy models.
This is the true root of the migration history: fresh databases can be brought
up to date with `alembic upgrade head` without any pre-existing tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load_metadata():
    """Imports the application models so every table is registered on Base.metadata."""
    from backend.infrastructure.database import Base
    import backend.infrastructure.models  # noqa: F401 (core/security/task tables)
    import backend.memory.memory_manager  # noqa: F401 (canonical memory tables)
    return Base.metadata


def upgrade() -> None:
    # Idempotent baseline: creates ONLY the tables that do not exist yet, so it is
    # safe against databases that were created via create_all before Alembic was
    # introduced (stamped later) as well as against completely empty databases.
    metadata = _load_metadata()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    tables_to_create = [
        table for table in metadata.sorted_tables
        if table.name not in existing_tables
    ]
    if tables_to_create:
        metadata.create_all(bind=bind, tables=tables_to_create)


def downgrade() -> None:
    metadata = _load_metadata()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Drop in reverse dependency order, only tables this baseline owns.
    for table in reversed(metadata.sorted_tables):
        if table.name in existing_tables:
            table.drop(bind=bind)
