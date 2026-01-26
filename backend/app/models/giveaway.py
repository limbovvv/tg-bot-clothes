from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import GiveawayStatus


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rules_text: Mapped[str] = mapped_column(Text, nullable=False)
    required_channel: Mapped[str] = mapped_column(Text, nullable=False)
    draw_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[GiveawayStatus] = mapped_column(
        Enum(GiveawayStatus, name="giveaway_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_giveaway_active",
    Giveaway.status,
    unique=True,
    postgresql_where=text("status = 'active'"),
)
