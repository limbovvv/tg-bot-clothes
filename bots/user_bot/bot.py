import asyncio
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging
from backend.app.core.time import utcnow
from backend.app.db.session import SessionLocal
from backend.app.models.enums import EntryStatus, GiveawayStatus
from backend.app.models.entry import Entry
from backend.app.models.giveaway import Giveaway
from backend.app.services.audit_service import log_action
from backend.app.services.entry_service import create_entry, get_entry_for_user
from backend.app.services.giveaway_service import get_active_giveaway
from backend.app.services.user_service import mark_subscribed_verified, upsert_user
from bots.common import messages

router = Router()
# User bot should only react to direct/private messages, not group chat messages.
router.message.filter(F.chat.type == "private")

CHANNEL_RE = re.compile(r"(?:https?://)?t\\.me/([A-Za-z0-9_]+)")


class EntryStates(StatesGroup):
    waiting_screenshot = State()
    waiting_fio = State()
    waiting_phone = State()


def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="🎁 Розыгрыш"))
    kb.add(KeyboardButton(text="✅ Мой статус"))
    kb.add(KeyboardButton(text="⏰ Когда розыгрыш?"))
    kb.add(KeyboardButton(text="📌 Правила"))
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def format_date_only(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")


def phone_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="Отправить контакт", request_contact=True))
    kb.add(KeyboardButton(text="Отмена"))
    return kb.as_markup(resize_keyboard=True)


def normalize_channel(value: str) -> str | int:
    raw = value.strip()
    if raw.startswith("@"):
        return raw
    if raw.startswith("https://") or raw.startswith("http://") or "t.me/" in raw:
        match = CHANNEL_RE.search(raw)
        if match:
            return f"@{match.group(1)}"
    if raw.lstrip("-").isdigit():
        return int(raw)
    return f"@{raw}"


def is_subscribed(member) -> bool:
    if not member:
        return False
    if member.status in {"left", "kicked"}:
        return False
    if member.status == "restricted":
        return bool(getattr(member, "is_member", False))
    return True


async def ensure_user(session: AsyncSession, message: Message) -> None:
    user = message.from_user
    if not user:
        return
    await upsert_user(session, tg_id=user.id, username=user.username)


@router.message(CommandStart())
async def start_handler(message: Message):
    async with SessionLocal() as session:
        await ensure_user(session, message)
        await session.commit()
    await message.answer(messages.WELCOME, reply_markup=main_menu())


@router.message(F.text == "🎁 Розыгрыш")
async def giveaway_handler(message: Message, state: FSMContext):
    async with SessionLocal() as session:
        await ensure_user(session, message)
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer(messages.NO_ACTIVE_GIVEAWAY, reply_markup=main_menu())
            await session.commit()
            return
        if not message.from_user or not message.from_user.username:
            await message.answer(messages.NEED_USERNAME, reply_markup=main_menu())
            await session.commit()
            return
        existing = await get_entry_for_user(
            session, giveaway_id=giveaway.id, tg_id=message.from_user.id
        )
        if existing:
            await message.answer(messages.ENTRY_ALREADY_EXISTS, reply_markup=main_menu())
            await session.commit()
            return

        try:
            chat_ref = normalize_channel(giveaway.required_channel)
            member = await message.bot.get_chat_member(chat_ref, message.from_user.id)
        except Exception:
            member = None
        if not is_subscribed(member):
            kb = InlineKeyboardBuilder()
            kb.button(
                text="Проверить подписку", callback_data=f"check_sub:{giveaway.id}"
            )
            await message.answer(
                messages.SUBSCRIBE_REQUIRED.format(channel=giveaway.required_channel),
                reply_markup=kb.as_markup(),
            )
            await session.commit()
            return

        await mark_subscribed_verified(session, tg_id=message.from_user.id)
        await state.set_state(EntryStates.waiting_screenshot)
        await state.update_data(giveaway_id=giveaway.id)
        await message.answer(
            messages.RULES_HEADER.format(rules=giveaway.rules_text),
            reply_markup=ReplyKeyboardRemove(),
        )
        await session.commit()


@router.callback_query(F.data.startswith("check_sub:"))
async def check_subscription(callback: CallbackQuery, state: FSMContext):
    if not callback.from_user:
        return
    if not callback.from_user.username:
        await callback.message.answer(messages.NEED_USERNAME)
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        await upsert_user(
            session, tg_id=callback.from_user.id, username=callback.from_user.username
        )
        giveaway = await session.get(Giveaway, giveaway_id)
        if (
            not giveaway
            or giveaway.id != giveaway_id
            or giveaway.status != GiveawayStatus.active
        ):
            giveaway = await get_active_giveaway(session)
        if not giveaway:
            await callback.message.answer(messages.NO_ACTIVE_GIVEAWAY)
            await callback.answer()
            await session.commit()
            return
        try:
            chat_ref = normalize_channel(giveaway.required_channel)
            member = await callback.bot.get_chat_member(chat_ref, callback.from_user.id)
        except Exception:
            member = None
        if not is_subscribed(member):
            await callback.answer("Подписка не найдена", show_alert=True)
            await session.commit()
            return

        await mark_subscribed_verified(session, tg_id=callback.from_user.id)
        await state.set_state(EntryStates.waiting_screenshot)
        await state.update_data(giveaway_id=giveaway.id)
        await callback.message.answer(
            messages.RULES_HEADER.format(rules=giveaway.rules_text),
            reply_markup=ReplyKeyboardRemove(),
        )
        await session.commit()
    await callback.answer()


@router.message(EntryStates.waiting_screenshot)
async def screenshot_handler(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer(messages.SCREENSHOT_ONLY)
        return
    file_id = message.photo[-1].file_id
    await state.update_data(screenshot_file_id=file_id)
    await state.set_state(EntryStates.waiting_fio)
    await message.answer(messages.ASK_FIO)


@router.message(EntryStates.waiting_fio)
async def fio_handler(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(messages.ASK_FIO)
        return
    await state.update_data(fio=message.text.strip())
    await state.set_state(EntryStates.waiting_phone)
    await message.answer(messages.ASK_PHONE, reply_markup=phone_keyboard())


@router.message(EntryStates.waiting_phone)
async def phone_handler(message: Message, state: FSMContext):
    phone = None
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        if message.text.strip().lower() == "отмена":
            await state.clear()
            await message.answer("Операция отменена.", reply_markup=main_menu())
            return
        phone = message.text.strip()
    if not phone:
        await message.answer(messages.ASK_PHONE, reply_markup=phone_keyboard())
        return

    data = await state.get_data()
    giveaway_id = data.get("giveaway_id")
    screenshot_file_id = data.get("screenshot_file_id")
    fio = data.get("fio")

    if not message.from_user:
        return

    async with SessionLocal() as session:
        entry = await create_entry(
            session,
            giveaway_id=giveaway_id,
            tg_id=message.from_user.id,
            screenshot_file_id=screenshot_file_id,
            fio=fio,
            phone=phone,
        )
        await session.commit()
        await session.refresh(entry)

    await state.clear()
    await message.answer(messages.ENTRY_CREATED, reply_markup=main_menu())

    username = message.from_user.username or "нет username"
    caption = (
        f"Новая заявка #{entry.id}\n"
        f"ФИО: {fio}\n"
        f"Телефон: {phone}\n"
        f"@{username}\n"
        f"tg_id: {message.from_user.id}\n"
        f"Время: {datetime.utcnow().isoformat()}"
    )
    await message.bot.send_photo(
        settings.admin_group_id,
        photo=screenshot_file_id,
        caption=caption,
        reply_markup=moderation_action_kb(entry.id),
    )


@router.message(F.text == "✅ Мой статус")
async def status_handler(message: Message):
    async with SessionLocal() as session:
        await ensure_user(session, message)
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer(messages.NO_ACTIVE_GIVEAWAY)
            await session.commit()
            return
        entry = await get_entry_for_user(
            session, giveaway_id=giveaway.id, tg_id=message.from_user.id
        )
        if not entry:
            await message.answer(messages.STATUS_NONE)
            await session.commit()
            return
        if entry.status == EntryStatus.pending:
            await message.answer(messages.STATUS_PENDING)
        elif entry.status == EntryStatus.approved:
            await message.answer(messages.STATUS_APPROVED)
        else:
            reason = entry.reject_reason_text or entry.reject_reason_code or "Без причины"
            await message.answer(messages.STATUS_REJECTED.format(reason=reason))
        await session.commit()


@router.message(F.text == "⏰ Когда розыгрыш?")
async def draw_time_handler(message: Message):
    async with SessionLocal() as session:
        await ensure_user(session, message)
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer(messages.NO_ACTIVE_GIVEAWAY)
            await session.commit()
            return
        if not giveaway.draw_at:
            await message.answer(messages.DRAW_NOT_SET)
            await session.commit()
            return
        await message.answer(messages.DRAW_AT.format(dt=format_date_only(giveaway.draw_at)))
        await session.commit()


@router.message(F.text == "📌 Правила")
async def rules_handler(message: Message):
    async with SessionLocal() as session:
        await ensure_user(session, message)
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer(messages.NO_ACTIVE_GIVEAWAY)
            await session.commit()
            return
        await message.answer(messages.RULES_TEXT.format(rules=giveaway.rules_text))
        await session.commit()


REJECT_REASONS = {
    "offensive": "Оскорбительный контент",
    "not_match": "Не соответствует условиям",
    "unreadable": "Не читается",
    "duplicate": "Дубликат",
    "no_reason": "Без причины",
    "custom": "Своя причина",
}


class RejectStates(StatesGroup):
    waiting_custom_reason = State()

def moderation_action_kb(entry_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Одобрить", callback_data=f"approve:{entry_id}")
    kb.button(text="❌ Отклонить", callback_data=f"reject:{entry_id}")
    kb.adjust(2)
    return kb.as_markup()


def moderation_edit_kb(entry_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="Изменить", callback_data=f"moderation_edit:{entry_id}")
    return kb.as_markup()


@router.callback_query(F.data.startswith("approve:"))
async def approve_callback(callback: CallbackQuery):
    entry_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        entry = await session.get(Entry, entry_id)
        if entry:
            entry.status = EntryStatus.approved
            entry.moderated_at = utcnow()
            entry.moderated_by = callback.from_user.id if callback.from_user else None
            await log_action(
                session,
                actor_tg_id=callback.from_user.id if callback.from_user else 0,
                action="entry_approve",
                payload={"entry_id": entry.id},
            )
            await session.commit()

    if entry:
        await callback.bot.send_message(entry.tg_id, messages.MODERATION_APPROVED)
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=moderation_edit_kb(entry.id)
            )
    await callback.answer("Одобрено")


@router.callback_query(F.data.startswith("reject:"))
async def reject_callback(callback: CallbackQuery, state: FSMContext):
    entry_id = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardBuilder()
    for code, label in REJECT_REASONS.items():
        kb.button(text=label, callback_data=f"reject_reason:{entry_id}:{code}")
    kb.adjust(1)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
        await state.update_data(
            moderation_chat_id=callback.message.chat.id,
            moderation_message_id=callback.message.message_id,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("reject_reason:"))
async def reject_reason_callback(callback: CallbackQuery, state: FSMContext):
    _, entry_id_str, code = callback.data.split(":", 2)
    entry_id = int(entry_id_str)
    if code == "custom":
        await state.set_state(RejectStates.waiting_custom_reason)
        await state.update_data(entry_id=entry_id)
        if callback.message:
            await callback.message.answer("Введите причину отклонения текстом.")
        await callback.answer()
        return

    reason = REJECT_REASONS.get(code)
    await apply_reject(callback, entry_id, code, reason)


@router.message(RejectStates.waiting_custom_reason)
async def custom_reason_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    entry_id = data.get("entry_id")
    moderation_chat_id = data.get("moderation_chat_id")
    moderation_message_id = data.get("moderation_message_id")
    reason_text = message.text.strip() if message.text else ""
    await apply_reject_message(
        message,
        entry_id,
        "custom",
        reason_text,
        moderation_chat_id=moderation_chat_id,
        moderation_message_id=moderation_message_id,
    )
    await state.clear()


async def apply_reject(callback: CallbackQuery, entry_id: int, code: str, reason: str | None):
    async with SessionLocal() as session:
        entry = await session.get(Entry, entry_id)
        if entry:
            entry.status = EntryStatus.rejected
            entry.reject_reason_code = code
            entry.reject_reason_text = reason
            entry.moderated_at = utcnow()
            entry.moderated_by = callback.from_user.id if callback.from_user else None
            await log_action(
                session,
                actor_tg_id=callback.from_user.id if callback.from_user else 0,
                action="entry_reject",
                payload={
                    "entry_id": entry.id,
                    "reason_code": code,
                    "reason_text": reason,
                },
            )
            await session.commit()

    if entry:
        await callback.bot.send_message(
            entry.tg_id, messages.MODERATION_REJECTED.format(reason=reason or "Без причины")
        )
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=moderation_edit_kb(entry.id)
            )
    await callback.answer("Отклонено")


async def apply_reject_message(
    message: Message,
    entry_id: int,
    code: str,
    reason: str | None,
    *,
    moderation_chat_id: int | None = None,
    moderation_message_id: int | None = None,
):
    async with SessionLocal() as session:
        entry = await session.get(Entry, entry_id)
        if entry:
            entry.status = EntryStatus.rejected
            entry.reject_reason_code = code
            entry.reject_reason_text = reason
            entry.moderated_at = utcnow()
            entry.moderated_by = message.from_user.id if message.from_user else None
            await log_action(
                session,
                actor_tg_id=message.from_user.id if message.from_user else 0,
                action="entry_reject",
                payload={
                    "entry_id": entry.id,
                    "reason_code": code,
                    "reason_text": reason,
                },
            )
            await session.commit()

    if entry:
        await message.bot.send_message(
            entry.tg_id, messages.MODERATION_REJECTED.format(reason=reason or "Без причины")
        )
    await message.answer("Отклонено")
    if moderation_chat_id and moderation_message_id:
        await message.bot.edit_message_reply_markup(
            chat_id=moderation_chat_id,
            message_id=moderation_message_id,
            reply_markup=moderation_edit_kb(entry_id),
        )


@router.callback_query(F.data.startswith("moderation_edit:"))
async def moderation_edit(callback: CallbackQuery):
    entry_id = int(callback.data.split(":", 1)[1])
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=moderation_action_kb(entry_id)
        )
    await callback.answer()


def run() -> None:
    setup_logging()
    bot = Bot(
        token=settings.user_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    run()
