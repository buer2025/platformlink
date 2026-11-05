"""leases for multi-process deployment, conversation summaries

Revision ID: c3d1e2f4a5b6
Revises: 7819424dff16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d1e2f4a5b6"
down_revision: str | Sequence[str] | None = "7819424dff16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leases",
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("holder", sa.String(100), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_leases")),
    )
    with op.batch_alter_table("outbound") as b:
        b.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("incidents") as b:
        b.add_column(sa.Column("conv_summary", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("conv_summary_upto", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as b:
        b.drop_column("conv_summary_upto")
        b.drop_column("conv_summary")
    with op.batch_alter_table("outbound") as b:
        b.drop_column("claimed_at")
    op.drop_table("leases")
