from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Winner(Base):
    __tablename__ = "winners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("giveaways.id"))
    entry_id: Mapped[int] = mapped_column(Integer, ForeignKey("entries.id"))
    chosen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
