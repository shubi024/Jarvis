"""make jarvis_approvals task_id nullable

Revision ID: acdd811abc67
Revises: 0001_initial_schema
Create Date: 2026-08-26 00:36:11.571354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'acdd811abc67'
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent + dialect-safe: skips when the column is already nullable
    # (e.g., fresh databases created by the 0001 baseline) and uses
    # batch_alter_table so SQLite is supported alongside PostgreSQL.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        column = next(
            c for c in inspector.get_columns("jarvis_approvals")
            if c["name"] == "task_id"
        )
        if column.get("nullable"):
            return
    except Exception:
        pass

    with op.batch_alter_table("jarvis_approvals") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_nulls = False
    try:
        has_nulls = bind.execute(
            sa.text("SELECT COUNT(*) FROM jarvis_approvals WHERE task_id IS NULL")
        ).scalar() or 0
    except Exception:
        has_nulls = True  # unknown state: refuse to re-add NOT NULL

    if has_nulls:
        return

    with op.batch_alter_table("jarvis_approvals") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )

