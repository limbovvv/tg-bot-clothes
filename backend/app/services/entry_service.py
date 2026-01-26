from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.entry import Entry
from backend.app.models.enums import EntryStatus
from backend.app.services.errors import EntryExists


async def get_entry_for_user(
    session: AsyncSession, *, giveaway_id: int, tg_id: int
) -> Entry | None:
    result = await session.execute(
        select(Entry).where(Entry.giveaway_id == giveaway_id, Entry.tg_id == tg_id)
    )
    return result.scalar_one_or_none()


async def create_entry(
    session: AsyncSession,
    *,
    giveaway_id: int,
    tg_id: int,
    screenshot_file_id: str,
    fio: str,
    phone: str,
) -> Entry:
    existing = await get_entry_for_user(session, giveaway_id=giveaway_id, tg_id=tg_id)
    if existing:
        raise EntryExists("Entry already exists for user")

    entry = Entry(
        giveaway_id=giveaway_id,
        tg_id=tg_id,
        screenshot_file_id=screenshot_file_id,
        fio=fio,
        phone=phone,
        status=EntryStatus.pending,
        created_at=utcnow(),
    )
    session.add(entry)
    return entry


async def approve_entry(
    session: AsyncSession,
    *,
    entry_id: int,
    moderated_by: int,
) -> Entry:
    entry = await session.get(Entry, entry_id)
    if not entry:
        raise ValueError("Entry not found")

    entry.status = EntryStatus.approved
    entry.moderated_at = utcnow()
    entry.moderated_by = moderated_by
    entry.reject_reason_code = None
    entry.reject_reason_text = None
    return entry


async def reject_entry(
    session: AsyncSession,
    *,
    entry_id: int,
    moderated_by: int,
    reason_code: str | None,
    reason_text: str | None,
) -> Entry:
    entry = await session.get(Entry, entry_id)
    if not entry:
        raise ValueError("Entry not found")

    entry.status = EntryStatus.rejected
    entry.moderated_at = utcnow()
    entry.moderated_by = moderated_by
    entry.reject_reason_code = reason_code
    entry.reject_reason_text = reason_text
    return entry
