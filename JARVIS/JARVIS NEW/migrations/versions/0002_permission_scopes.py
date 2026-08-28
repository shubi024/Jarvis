"""add client/project isolation scopes to jarvis_permissions

The PermissionEngine performs exact client_scope/project_scope matching on
durable grants; these columns were missing from the original schema.

Revision ID: 0002_permission_scopes
Revises: acdd811abc67
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_permission_scopes'
down_revision: Union[str, None] = 'acdd811abc67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("client_scope", "project_scope")


def _missing_columns(bind, table: str) -> set:
    inspector = sa.inspect(bind)
    try:
        existing = {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return set(_COLUMNS)
    return {c for c in _COLUMNS if c not in existing}


def upgrade() -> None:
    bind = op.get_bind()
    missing = _missing_columns(bind, "jarvis_permissions")
    if not missing:
        return
    # batch_alter_table keeps this compatible with SQLite AND PostgreSQL.
    with op.batch_alter_table("jarvis_permissions") as batch:
        if "client_scope" in missing:
            batch.add_column(sa.Column("client_scope", sa.String(length=64), nullable=True))
            batch.create_index("ix_jarvis_permissions_client_scope", ["client_scope"])
        if "project_scope" in missing:
            batch.add_column(sa.Column("project_scope", sa.String(length=64), nullable=True))
            batch.create_index("ix_jarvis_permissions_project_scope", ["project_scope"])


def downgrade() -> None:
    bind = op.get_bind()
    missing = _missing_columns(bind, "jarvis_permissions")
    present = [c for c in _COLUMNS if c not in missing]
    if not present:
        return
    with op.batch_alter_table("jarvis_permissions") as batch:
        if "project_scope" in present:
            batch.drop_index("ix_jarvis_permissions_project_scope")
            batch.drop_column("project_scope")
        if "client_scope" in present:
            batch.drop_index("ix_jarvis_permissions_client_scope")
            batch.drop_column("client_scope")
