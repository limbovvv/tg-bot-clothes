"""init

Revision ID: 0001_init
Revises: 
Create Date: 2026-01-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    giveaway_status = postgresql.ENUM(
        "active", "closed", name="giveaway_status", create_type=False
    )
    entry_status = postgresql.ENUM(
        "pending", "approved", "rejected", name="entry_status", create_type=False
    )
    broadcast_segment = postgresql.ENUM(
        "all_bot_users",
        "approved_in_active_giveaway",
        "subscribed_verified",
        name="broadcast_segment",
        create_type=False,
    )
    broadcast_payload_type = postgresql.ENUM(
        "text",
        "photo",
        "video",
        "document",
        "video_note",
        name="broadcast_payload_type",
        create_type=False,
    )

    giveaway_status.create(op.get_bind(), checkfirst=True)
    entry_status.create(op.get_bind(), checkfirst=True)
    broadcast_segment.create(op.get_bind(), checkfirst=True)
    broadcast_payload_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("subscribed_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)

    op.create_table(
        "giveaways",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rules_text", sa.Text(), nullable=False),
        sa.Column("required_channel", sa.Text(), nullable=False),
        sa.Column("draw_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", giveaway_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_giveaway_active",
        "giveaways",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("giveaway_id", sa.Integer(), sa.ForeignKey("giveaways.id"), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), sa.ForeignKey("users.tg_id"), nullable=False),
        sa.Column("screenshot_file_id", sa.Text(), nullable=False),
        sa.Column("fio", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("status", entry_status, nullable=False),
        sa.Column("reject_reason_code", sa.String(length=64), nullable=True),
        sa.Column("reject_reason_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moderated_by", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("giveaway_id", "tg_id", name="uq_entries_giveaway_user"),
    )
    op.create_index("ix_entries_giveaway_id", "entries", ["giveaway_id"], unique=False)
    op.create_index("ix_entries_status", "entries", ["status"], unique=False)
    op.create_index("ix_entries_created_at", "entries", ["created_at"], unique=False)

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("segment", broadcast_segment, nullable=False),
        sa.Column("payload_type", broadcast_payload_type, nullable=False),
        sa.Column("payload_file_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_ok", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sent_fail", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "winners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("giveaway_id", sa.Integer(), sa.ForeignKey("giveaways.id"), nullable=False),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("entries.id"), nullable=False),
        sa.Column("chosen_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("admin_audit_log")
    op.drop_table("winners")
    op.drop_table("broadcasts")
    op.drop_index("ix_entries_created_at", table_name="entries")
    op.drop_index("ix_entries_status", table_name="entries")
    op.drop_index("ix_entries_giveaway_id", table_name="entries")
    op.drop_table("entries")
    op.drop_index("uq_giveaway_active", table_name="giveaways")
    op.drop_table("giveaways")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS giveaway_status")
    op.execute("DROP TYPE IF EXISTS entry_status")
    op.execute("DROP TYPE IF EXISTS broadcast_segment")
    op.execute("DROP TYPE IF EXISTS broadcast_payload_type")
