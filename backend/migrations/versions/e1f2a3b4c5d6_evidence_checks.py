"""incident evidence checklist

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as b:
        b.add_column(sa.Column("evidence_checks", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as b:
        b.drop_column("evidence_checks")
