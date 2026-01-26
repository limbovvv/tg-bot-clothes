from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class AdminLoginAttempt(Base):
    __tablename__ = "admin_login_attempts"
    __table_args__ = (
        UniqueConstraint("ip", "username", name="uq_admin_login_attempts_ip_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
