import asyncio
import random
from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.core.config import settings
from backend.app.core.time import utcnow
from backend.app.models.broadcast import Broadcast
from backend.app.models.admin_user import AdminUser
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
from backend.app.services.winner_service import create_winner
from backend.app.services.user_service import mark_blocked, mark_subscribed_verified
from worker.celery_app import celery_app


@asynccontextmanager
async def worker_session():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        yield session
    await engine.dispose()


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
        async with worker_session() as session:
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
        async with worker_session() as session:
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
        async with worker_session() as session:
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


def _add_one_month(dt: datetime) -> datetime:
    year = dt.year
    month = dt.month + 1
    if month == 13:
        month = 1
        year += 1
    last_day = monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def _month_run_at(day_of_month: int, now: datetime) -> datetime:
    safe_day = max(1, min(day_of_month, 28))
    candidate = datetime(now.year, now.month, safe_day, 0, 5, tzinfo=timezone.utc)
    # If we are already past the configured day in this month, plan next month.
    # If we are on the same day but later than 00:05, we should still run today.
    if now.date() > candidate.date():
        candidate = _add_one_month(candidate)
    return candidate


async def _fetch_admin_tg_ids(session) -> list[int]:
    rows = (
        await session.execute(
            select(User.tg_id)
            .select_from(User)
            .join(AdminUser, AdminUser.username == User.username)
            .where(AdminUser.is_active.is_(True), User.is_blocked.is_(False))
            .distinct()
        )
    ).all()
    return [row[0] for row in rows]


async def _announce_start(giveaway: Giveaway) -> None:
    channel_text = (
        "🎉 Розыгрыш начался!\n"
        f"Название: {giveaway.title}\n"
        "Участвовать можно в боте."
    )
    bot_text = (
        "🎉 Новый розыгрыш начался!\n"
        f"{giveaway.title}\n"
        "Зайдите в бота и подайте заявку."
    )
    admin_text = (
        "✅ Автоматический розыгрыш запущен.\n"
        f"ID: {giveaway.id}\n"
        f"Название: {giveaway.title}"
    )
    if settings.public_channel:
        async with Bot(
            token=settings.admin_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as admin_bot:
            try:
                await admin_bot.send_message(settings.public_channel, channel_text)
            except Exception:
                pass
    celery_app.send_task("worker.tasks.send_broadcast_text", args=[bot_text])
    async with worker_session() as session:
        admin_ids = await _fetch_admin_tg_ids(session)
    if admin_ids:
        async with Bot(
            token=settings.admin_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as admin_bot:
            for admin_id in admin_ids:
                try:
                    await admin_bot.send_message(admin_id, admin_text)
                except Exception:
                    pass


async def _draw_and_notify(active: Giveaway, session) -> dict:
    rows = (
        await session.execute(
            select(Entry, User)
            .join(User, User.tg_id == Entry.tg_id)
            .where(
                Entry.giveaway_id == active.id,
                Entry.status == EntryStatus.approved,
                User.username.is_not(None),
                User.is_blocked.is_(False),
            )
        )
    ).all()
    if not rows:
        return {"winner_username": None, "winner_tg_id": None}
    entry, user = random.choice(rows)
    await create_winner(session, giveaway_id=active.id, entry_id=entry.id)
    winner_username = user.username or ""
    winner_tg_id = user.tg_id
    public_text = (
        "🎉🌟 ПОБЕДИТЕЛЬ РОЗЫГРЫША! 🌟🎉\n\n"
        f"🏆 Победитель: @{winner_username}"
        if winner_username
        else None
    )
    broadcast_text = (
        f"🎉 Розыгрыш завершен!\nПобедитель: @{winner_username}\n"
        "Новый розыгрыш уже начался."
        if winner_username
        else "🎉 Розыгрыш завершен! Новый розыгрыш уже начался."
    )
    if public_text and settings.public_channel:
        async with Bot(
            token=settings.admin_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as admin_bot:
            try:
                await admin_bot.send_message(settings.public_channel, public_text)
            except Exception:
                pass
    async with Bot(
        token=settings.user_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as user_bot:
        if winner_username:
            try:
                await user_bot.send_message(
                    winner_tg_id,
                    "🎉 Поздравляем! Вы победитель розыгрыша.",
                )
            except Exception:
                pass
    celery_app.send_task("worker.tasks.send_broadcast_text", args=[broadcast_text])
    return {"winner_username": winner_username, "winner_tg_id": winner_tg_id}


@celery_app.task(name="worker.tasks.automation_rollover_check")
def automation_rollover_check() -> None:
    asyncio.run(_automation_rollover_check_async())


async def _automation_rollover_check_async() -> None:
    now = utcnow()
    async with worker_session() as session:
        settings_row = await get_automation_settings(session)
        if not settings_row.is_enabled:
            await session.commit()
            return
        if not settings_row.required_channel or not settings_row.rules_text:
            await session.commit()
            return

        if settings_row.start_at:
            run_at = settings_row.start_at
            if now < run_at:
                await session.commit()
                return
            if settings_row.last_run_at and settings_row.last_run_at >= run_at:
                await session.commit()
                return
            next_run_at = _add_one_month(run_at)
        else:
            run_at = _month_run_at(settings_row.day_of_month, now)
            if now < run_at:
                await session.commit()
                return
            if not await should_run_for_month(settings_row, run_at):
                await session.commit()
                return
            next_run_at = _add_one_month(run_at)

        active = await get_active_giveaway(session)
        winner_info = {"winner_username": None, "winner_tg_id": None}
        if active:
            winner_info = await _draw_and_notify(active, session)
            await close_giveaway(session, giveaway_id=active.id)

        title = _format_title(settings_row.title_template, run_at)
        draw_at = next_run_at + timedelta(days=settings_row.draw_offset_days)
        giveaway = await create_giveaway(
            session,
            title=title,
            rules_text=settings_row.rules_text,
            required_channel=settings_row.required_channel,
            draw_at=draw_at,
        )
        await _announce_start(giveaway)
        if settings_row.start_at:
            settings_row.start_at = next_run_at
        await mark_run_month(session, settings_row, run_at)
        await log_action(
            session,
            actor_tg_id=0,
            action="automation_rollover",
            payload={
                "giveaway_id": giveaway.id,
                "title": title,
                "run_at": run_at.isoformat(),
                "next_run_at": next_run_at.isoformat(),
                "winner_username": winner_info["winner_username"],
            },
        )
        await session.commit()
