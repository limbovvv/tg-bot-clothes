import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.time import utcnow
from backend.app.db.session import SessionLocal
from backend.app.models.broadcast import Broadcast
from backend.app.models.entry import Entry
from backend.app.models.giveaway import Giveaway
from backend.app.models.enums import (
    BroadcastPayloadType,
    BroadcastSegment,
    EntryStatus,
    GiveawayStatus,
)
from backend.app.models.user import User
from backend.app.services.automation_service import (
    get_automation_settings,
    mark_run_month,
    should_run_for_month,
)
from backend.app.services.audit_service import log_action
from backend.app.services.giveaway_service import (
    close_giveaway,
    create_giveaway,
    get_active_giveaway,
)
from backend.app.services.user_service import mark_blocked, mark_subscribed_verified
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


def _is_channel_member(status: ChatMemberStatus | str) -> bool:
    return status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    }


async def _collect_recipients(session, bot: Bot, broadcast: Broadcast) -> list[int]:
    if broadcast.segment == BroadcastSegment.all_bot_users:
        rows = (
            await session.execute(select(User.tg_id).where(User.is_blocked.is_(False)))
        ).all()
        return [row[0] for row in rows]
    if broadcast.segment == BroadcastSegment.subscribed_verified:
        if not settings.public_channel:
            return []
        rows = (
            await session.execute(select(User.tg_id).where(User.is_blocked.is_(False)))
        ).all()
        recipients: list[int] = []
        for row in rows:
            tg_id = row[0]
            try:
                member = await bot.get_chat_member(settings.public_channel, tg_id)
            except Exception:
                continue
            if _is_channel_member(member.status):
                recipients.append(tg_id)
                await mark_subscribed_verified(session, tg_id=tg_id)
        await session.commit()
        return recipients

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
            if broadcast.started_at is None:
                broadcast.started_at = utcnow()
                await session.commit()
            recipients = await _collect_recipients(session, bot, broadcast)
            sent_ok = 0
            sent_fail = 0
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate

            for idx, tg_id in enumerate(recipients, start=1):
                if idx % 10 == 0:
                    await session.refresh(broadcast)
                if broadcast.is_cancelled:
                    break
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
            broadcast.started_at = utcnow()
            recipients = (
                await session.execute(
                    select(User.tg_id).where(User.is_blocked.is_(False))
                )
            ).all()
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate
            sent_ok = 0
            sent_fail = 0
            for idx, row in enumerate(recipients, start=1):
                tg_id = row[0]
                if idx % 10 == 0:
                    await session.refresh(broadcast)
                if broadcast.is_cancelled:
                    break
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
            broadcast.started_at = utcnow()
            query = select(User.tg_id).where(User.is_blocked.is_(False))
            if exclude_tg_ids:
                query = query.where(User.tg_id.not_in(exclude_tg_ids))
            recipients = (await session.execute(query)).all()
            rate = max(settings.broadcast_rate_per_sec, 1)
            delay = 1 / rate
            sent_ok = 0
            sent_fail = 0
            for idx, row in enumerate(recipients, start=1):
                tg_id = row[0]
                if idx % 10 == 0:
                    await session.refresh(broadcast)
                if broadcast.is_cancelled:
                    break
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


def _format_title(template: str, now: datetime) -> str:
    month_names = [
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    ]
    month_index = now.month - 1
    month_name = month_names[month_index]
    safe_template = template or "Ежемесячный розыгрыш"
    try:
        return safe_template.format(
            month=now.month,
            month_name=month_name,
            year=now.year,
        )
    except Exception:
        return safe_template


@celery_app.task(name="worker.tasks.automation_rollover_check")
def automation_rollover_check() -> None:
    asyncio.run(_automation_rollover_check_async())


async def _automation_rollover_check_async() -> None:
    now = utcnow()
    async with SessionLocal() as session:
        settings_row = await get_automation_settings(session)
        if not settings_row.is_enabled:
            await session.commit()
            return
        if not settings_row.required_channel or not settings_row.rules_text:
            await session.commit()
            return

        # If an exact start datetime is set, prefer it over day-of-month logic.
        if settings_row.start_at:
            if now < settings_row.start_at:
                await session.commit()
                return
            if settings_row.last_run_at and settings_row.last_run_at >= settings_row.start_at:
                await session.commit()
                return
        else:
            if now.day != settings_row.day_of_month:
                await session.commit()
                return
            if not await should_run_for_month(settings_row, now):
                await session.commit()
                return

        active = await get_active_giveaway(session)
        if active:
            await close_giveaway(session, giveaway_id=active.id)

        title = _format_title(settings_row.title_template, now)
        draw_at = now + timedelta(days=settings_row.draw_offset_days)
        giveaway = await create_giveaway(
            session,
            title=title,
            rules_text=settings_row.rules_text,
            required_channel=settings_row.required_channel,
            draw_at=draw_at,
        )
        await mark_run_month(session, settings_row, now)
        await log_action(
            session,
            actor_tg_id=0,
            action="automation_rollover",
            payload={
                "giveaway_id": giveaway.id,
                "title": title,
                "day_of_month": settings_row.day_of_month,
            },
        )
        await session.commit()
