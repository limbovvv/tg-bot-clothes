from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.enums import GiveawayStatus
from backend.app.models.giveaway import Giveaway
from backend.app.services.errors import ActiveGiveawayExists, GiveawayNotFound


async def get_active_giveaway(session: AsyncSession) -> Giveaway | None:
    result = await session.execute(
        select(Giveaway).where(Giveaway.status == GiveawayStatus.active)
    )
    return result.scalar_one_or_none()


async def create_giveaway(
    session: AsyncSession,
    *,
    title: str,
    rules_text: str,
    required_channel: str,
    draw_at,
) -> Giveaway:
    existing = await get_active_giveaway(session)
    if existing:
        raise ActiveGiveawayExists("Active giveaway already exists")

    giveaway = Giveaway(
        title=title,
        rules_text=rules_text,
        required_channel=required_channel,
        draw_at=draw_at,
        status=GiveawayStatus.active,
        created_at=utcnow(),
    )
    session.add(giveaway)
    return giveaway


async def update_giveaway(
    session: AsyncSession,
    *,
    giveaway_id: int,
    rules_text: str | None = None,
    draw_at=None,
    required_channel: str | None = None,
) -> Giveaway:
    giveaway = await session.get(Giveaway, giveaway_id)
    if not giveaway:
        raise GiveawayNotFound("Giveaway not found")

    if rules_text is not None:
        giveaway.rules_text = rules_text
    if draw_at is not None:
        giveaway.draw_at = draw_at
    if required_channel is not None:
        giveaway.required_channel = required_channel
    return giveaway


async def close_giveaway(session: AsyncSession, *, giveaway_id: int) -> Giveaway:
    giveaway = await session.get(Giveaway, giveaway_id)
    if not giveaway:
        raise GiveawayNotFound("Giveaway not found")

    giveaway.status = GiveawayStatus.closed
    giveaway.closed_at = utcnow()
    return giveaway
