"""broadcast status fields

Revision ID: 0004_broadcast_status_fields
Revises: 0003_admin_login_attempts
Create Date: 2026-01-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0004_broadcast_status_fields"
down_revision = "0003_admin_login_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broadcasts",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "broadcasts",
        sa.Column("is_cancelled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "broadcasts",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broadcasts", "cancelled_at")
    op.drop_column("broadcasts", "is_cancelled")
    op.drop_column("broadcasts", "started_at")
