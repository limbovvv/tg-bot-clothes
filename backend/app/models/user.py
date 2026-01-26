from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscribed_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
