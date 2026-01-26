from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.winner import Winner


async def create_winner(
    session: AsyncSession, *, giveaway_id: int, entry_id: int
) -> Winner:
    winner = Winner(giveaway_id=giveaway_id, entry_id=entry_id, chosen_at=utcnow())
    session.add(winner)
    return winner
