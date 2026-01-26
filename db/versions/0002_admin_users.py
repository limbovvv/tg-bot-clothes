"""admin users

Revision ID: 0002_admin_users
Revises: 0001_init
Create Date: 2026-01-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0002_admin_users"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_admin_users_username", "admin_users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_admin_users_username", table_name="admin_users")
    op.drop_table("admin_users")
