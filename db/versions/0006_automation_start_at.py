"""automation start at

Revision ID: 0006_auto_start
Revises: 0005_giveaway_auto
Create Date: 2026-01-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0006_auto_start"
down_revision = "0005_giveaway_auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "giveaway_automation_settings",
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "giveaway_automation_settings",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("giveaway_automation_settings", "last_run_at")
    op.drop_column("giveaway_automation_settings", "start_at")
