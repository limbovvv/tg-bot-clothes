"""giveaway automation settings

Revision ID: 0005_giveaway_auto
Revises: 0004_broadcast_status_fields
Create Date: 2026-01-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0005_giveaway_auto"
down_revision = "0004_broadcast_status_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "giveaway_automation_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title_template", sa.Text(), nullable=False, server_default="Ежемесячный розыгрыш"),
        sa.Column("rules_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_channel", sa.Text(), nullable=False, server_default=""),
        sa.Column("draw_offset_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_month", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("giveaway_automation_settings")
