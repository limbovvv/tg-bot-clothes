from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.broadcast import Broadcast
from backend.app.models.enums import BroadcastPayloadType, BroadcastSegment


async def create_broadcast(
    session: AsyncSession,
    *,
    created_by: int,
    segment: BroadcastSegment,
    payload_type: BroadcastPayloadType,
    payload_file_id: str | None,
    text: str | None,
) -> Broadcast:
    broadcast = Broadcast(
        created_by=created_by,
        segment=segment,
        payload_type=payload_type,
        payload_file_id=payload_file_id,
        text=text,
        created_at=utcnow(),
        sent_ok=0,
        sent_fail=0,
    )
    session.add(broadcast)
    return broadcast


async def mark_broadcast_sent(
    session: AsyncSession, *, broadcast_id: int, sent_ok: int, sent_fail: int
) -> Broadcast:
    broadcast = await session.get(Broadcast, broadcast_id)
    if not broadcast:
        raise ValueError("Broadcast not found")

    broadcast.sent_at = utcnow()
    broadcast.sent_ok = sent_ok
    broadcast.sent_fail = sent_fail
    return broadcast
