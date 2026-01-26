from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.user import User


async def upsert_user(session: AsyncSession, *, tg_id: int, username: str | None) -> User:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()
    now = utcnow()
    if user:
        user.username = username
        user.last_seen_at = now
        return user

    user = User(
        tg_id=tg_id,
        username=username,
        first_seen_at=now,
        last_seen_at=now,
        is_blocked=False,
    )
    session.add(user)
    return user


async def mark_blocked(session: AsyncSession, *, tg_id: int) -> None:
    user = await session.get(User, tg_id)
    if user:
        user.is_blocked = True


async def mark_subscribed_verified(session: AsyncSession, *, tg_id: int) -> None:
    user = await session.get(User, tg_id)
    if user:
        user.subscribed_verified_at = utcnow()
