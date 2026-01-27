from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class GiveawayAutomationSettings(Base):
    __tablename__ = "giveaway_automation_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title_template: Mapped[str] = mapped_column(Text, nullable=False, default="Ежемесячный розыгрыш")
    rules_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    required_channel: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draw_offset_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_month: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
