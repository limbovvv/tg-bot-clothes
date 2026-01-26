import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.time import utcnow
from backend.app.db.session import SessionLocal
from backend.app.models.broadcast import Broadcast
from backend.app.models.entry import Entry
from backend.app.models.enums import (
    BroadcastPayloadType,
    BroadcastSegment,
    EntryStatus,
    GiveawayStatus,
)
from backend.app.models.giveaway import Giveaway
from backend.app.models.user import User
from backend.app.services.user_service import mark_blocked
from worker.celery_app import celery_app


async def _send_payload(bot: Bot, tg_id: int, broadcast: Broadcast) -> None:
    if broadcast.payload_type == BroadcastPayloadType.text:
        await bot.send_message(tg_id, broadcast.text or "")
    elif broadcast.payload_type == BroadcastPayloadType.photo:
        await bot.send_photo(tg_id, broadcast.payload_file_id, caption=broadcast.text)
    elif broadcast.payload_type == BroadcastPayloadType.video:
        await bot.send_video(tg_id, broadcast.payload_file_id, caption=broadcast.text)
    elif broadcast.payload_type == BroadcastPayloadType.document:
        await bot.send_document(tg_id, broadcast.payload_file_id, caption=broadcast.text)
    elif broadcast.payload_type == BroadcastPayloadType.video_note:
        await bot.send_video_note(tg_id, broadcast.payload_file_id)


async def _collect_recipients(session, broadcast: Broadcast) -> list[int]:
    if broadcast.segment == BroadcastSegment.all_bot_users:
        rows = (
            await session.execute(select(User.tg_id).where(User.is_blocked.is_(False)))
        ).all()
        return [row[0] for row in rows]
    if broadcast.segment == BroadcastSegment.subscribed_verified:
        rows = (
            await session.execute(
                select(User.tg_id).where(
                    User.is_blocked.is_(False), User.subscribed_verified_at.is_not(None)
                )
            )
        ).all()
        return [row[0] for row in rows]

    giveaway = (
        await session.execute(
            select(Giveaway).where(Giveaway.status == GiveawayStatus.active)
        )
    ).scalar_one_or_none()
    if not giveaway:
        return []

    rows = (
        await session.execute(
            select(User.tg_id)
            .join(Entry, Entry.tg_id == User.tg_id)
            .where(
                Entry.giveaway_id == giveaway.id,
                Entry.status == EntryStatus.approved,
                User.is_blocked.is_(False),
            )
        )
    ).all()
    return [row[0] for row in rows]


@celery_app.task(name="worker.tasks.send_broadcast")
def send_broadcast(broadcast_id: int) -> None:
    asyncio.run(_send_broadcast_async(broadcast_id))


async def _send_broadcast_async(broadcast_id: int) -> None:
    async with Bot(
        token=settings.user_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:
        async with SessionLocal() as session:
            broadcast = await session.get(Broadcast, broadcast_id)
            if not broadcast:
                return
            recipients = await _collect_recipients(session, broadcast)
            sent_ok = 0
            sent_fail = 0
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate

            for tg_id in recipients:
                try:
                    await _send_payload(bot, tg_id, broadcast)
                    sent_ok += 1
                except TelegramForbiddenError:
                    sent_fail += 1
                    await mark_blocked(session, tg_id=tg_id)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                except Exception:
                    sent_fail += 1
                await asyncio.sleep(delay)

            broadcast.sent_at = utcnow()
            broadcast.sent_ok = sent_ok
            broadcast.sent_fail = sent_fail
            await session.commit()


@celery_app.task(name="worker.tasks.send_broadcast_text")
def send_broadcast_text(text: str) -> None:
    asyncio.run(_send_broadcast_text_async(text))


async def _send_broadcast_text_async(text: str) -> None:
    async with Bot(
        token=settings.user_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:
        async with SessionLocal() as session:
            broadcast = Broadcast(
                created_by=0,
                segment=BroadcastSegment.all_bot_users,
                payload_type=BroadcastPayloadType.text,
                text=text,
                created_at=utcnow(),
            )
            session.add(broadcast)
            await session.flush()
            recipients = (
                await session.execute(
                    select(User.tg_id).where(User.is_blocked.is_(False))
                )
            ).all()
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate
            sent_ok = 0
            sent_fail = 0
            for row in recipients:
                tg_id = row[0]
                try:
                    await bot.send_message(tg_id, text)
                    sent_ok += 1
                except TelegramForbiddenError:
                    sent_fail += 1
                    await mark_blocked(session, tg_id=tg_id)
                except Exception:
                    sent_fail += 1
                    pass
                await asyncio.sleep(delay)
            broadcast.sent_at = utcnow()
            broadcast.sent_ok = sent_ok
            broadcast.sent_fail = sent_fail
            await session.commit()


@celery_app.task(name="worker.tasks.send_broadcast_text_exclude")
def send_broadcast_text_exclude(text: str, exclude_tg_ids: list[int]) -> None:
    asyncio.run(_send_broadcast_text_exclude_async(text, exclude_tg_ids))


async def _send_broadcast_text_exclude_async(
    text: str, exclude_tg_ids: list[int]
) -> None:
    async with Bot(
        token=settings.user_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:
        async with SessionLocal() as session:
            broadcast = Broadcast(
                created_by=0,
                segment=BroadcastSegment.all_bot_users,
                payload_type=BroadcastPayloadType.text,
                text=text,
                created_at=utcnow(),
            )
            session.add(broadcast)
            await session.flush()
            query = select(User.tg_id).where(User.is_blocked.is_(False))
            if exclude_tg_ids:
                query = query.where(User.tg_id.not_in(exclude_tg_ids))
            recipients = (await session.execute(query)).all()
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate
            sent_ok = 0
            sent_fail = 0
            for row in recipients:
                tg_id = row[0]
                try:
                    await bot.send_message(tg_id, text)
                    sent_ok += 1
                except TelegramForbiddenError:
                    sent_fail += 1
                    await mark_blocked(session, tg_id=tg_id)
                except Exception:
                    sent_fail += 1
                    pass
                await asyncio.sleep(delay)
            broadcast.sent_at = utcnow()
            broadcast.sent_ok = sent_ok
            broadcast.sent_fail = sent_fail
            await session.commit()
