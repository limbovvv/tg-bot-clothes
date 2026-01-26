"""admin login attempts

Revision ID: 0003_admin_login_attempts
Revises: 0002_admin_users
Create Date: 2026-01-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0003_admin_login_attempts"
down_revision = "0002_admin_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_admin_login_attempts_ip_username",
        "admin_login_attempts",
        ["ip", "username"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_admin_login_attempts_ip_username",
        "admin_login_attempts",
        type_="unique",
    )
    op.drop_table("admin_login_attempts")
