from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import EntryStatus


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "tg_id", name="uq_entries_giveaway_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("giveaways.id"))
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"))
    screenshot_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    fio: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EntryStatus] = mapped_column(
        Enum(EntryStatus, name="entry_status"), nullable=False
    )
    reject_reason_code: Mapped[str | None] = mapped_column(String(64))
    reject_reason_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[int | None] = mapped_column(BigInteger)


Index("ix_entries_giveaway_id", Entry.giveaway_id)
Index("ix_entries_status", Entry.status)
Index("ix_entries_created_at", Entry.created_at)
