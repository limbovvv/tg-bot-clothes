from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import BroadcastPayloadType, BroadcastSegment


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    segment: Mapped[BroadcastSegment] = mapped_column(
        Enum(BroadcastSegment, name="broadcast_segment"), nullable=False
    )
    payload_type: Mapped[BroadcastPayloadType] = mapped_column(
        Enum(BroadcastPayloadType, name="broadcast_payload_type"), nullable=False
    )
    payload_file_id: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_fail: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
