import asyncio
import random
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import func, select
import re

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging
from backend.app.db.session import SessionLocal
from backend.app.models.admin_user import AdminUser
from backend.app.core.time import utcnow
from backend.app.db.session import SessionLocal
from backend.app.models.entry import Entry
from backend.app.models.enums import (
    BroadcastPayloadType,
    BroadcastSegment,
    EntryStatus,
    GiveawayStatus,
)
from backend.app.models.giveaway import Giveaway
from backend.app.models.user import User
from backend.app.services.audit_service import log_action
from backend.app.services.automation_service import disable_automation
from backend.app.services.broadcast_service import create_broadcast
from backend.app.services.errors import ActiveGiveawayExists
from backend.app.services.giveaway_service import (
    close_giveaway,
    create_giveaway,
    get_active_giveaway,
    update_giveaway,
)
from backend.app.services.winner_service import create_winner
from worker.celery_app import celery_app

router = Router()
# Admin bot should only react to direct/private messages, not group chat messages.
router.message.filter(F.chat.type == "private")


async def is_admin_user(user) -> bool:
    if not user or not getattr(user, "username", None):
        return False
    username = user.username
    async with SessionLocal() as session:
        admin = (
            await session.execute(
                select(AdminUser).where(
                    AdminUser.username == username, AdminUser.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
    return admin is not None


async def ensure_admin(message: Message) -> bool:
    if not await is_admin_user(message.from_user):
        return False
    return True


def admin_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎁 Новый розыгрыш")
    kb.button(text="✏️ Редактировать розыгрыш")
    kb.button(text="🛑 Закрыть розыгрыш")
    kb.button(text="📊 Статистика")
    kb.button(text="📣 Рассылка")
    kb.button(text="🏆 Выбор победителя")
    kb.adjust(2, 2, 2)
    return kb.as_markup(resize_keyboard=True)


def parse_date_only(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d.%m.%Y")


def normalize_channel(value: str) -> str:
    value = value.strip()
    if value.startswith("https://t.me/"):
        value = "@" + value.replace("https://t.me/", "").strip().lstrip("@")
    elif value.startswith("t.me/"):
        value = "@" + value.replace("t.me/", "").strip().lstrip("@")
    return value


def is_valid_channel_username(value: str) -> bool:
    value = value.strip()
    return bool(re.match(r"^@[A-Za-z0-9_]{5,32}$", value))


def edit_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📝 Правила")
    kb.button(text="📣 Канал")
    kb.button(text="📅 Дата")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=False)


def back_only_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=False)


class GiveawayCreateStates(StatesGroup):
    title = State()
    channel = State()
    rules = State()
    draw_at = State()
    confirm = State()


class GiveawayEditStates(StatesGroup):
    choose = State()
    rules = State()
    channel = State()
    draw_at = State()
    confirm = State()


class BroadcastStates(StatesGroup):
    content = State()
    confirm = State()


class DrawStates(StatesGroup):
    count = State()
    confirm = State()


@router.message(Command("start"))
async def admin_start(message: Message):
    if not await is_admin_user(message.from_user):
        return
    await message.answer(
        "Админ-бот готов. Выберите действие кнопками или командами.",
        reply_markup=admin_menu(),
    )


@router.message(F.text == "🎁 Новый розыгрыш")
async def menu_giveaway_new(message: Message, state: FSMContext):
    await giveaway_new(message, state)


@router.message(F.text == "✏️ Редактировать розыгрыш")
async def menu_giveaway_edit(message: Message, state: FSMContext):
    await giveaway_edit(message, state)


@router.message(F.text == "🛑 Закрыть розыгрыш")
async def menu_giveaway_close(message: Message):
    await giveaway_close(message)


@router.message(F.text == "📊 Статистика")
async def menu_stats(message: Message):
    await stats_handler(message)


@router.message(F.text == "📣 Рассылка")
async def menu_broadcast(message: Message, state: FSMContext):
    await broadcast_start(message, state)


@router.message(F.text == "🏆 Выбор победителя")
async def menu_draw(message: Message, state: FSMContext):
    await draw_start(message, state)


@router.message(Command("giveaway_new"))
async def giveaway_new(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if giveaway:
            await message.answer(
                "Уже есть активный розыгрыш", reply_markup=admin_menu()
            )
            return
    await state.set_state(GiveawayCreateStates.title)
    await message.answer("Введите название розыгрыша:", reply_markup=back_only_menu())


@router.message(GiveawayCreateStates.title, F.text == "⬅️ Назад")
@router.message(GiveawayCreateStates.channel, F.text == "⬅️ Назад")
@router.message(GiveawayCreateStates.rules, F.text == "⬅️ Назад")
@router.message(GiveawayCreateStates.draw_at, F.text == "⬅️ Назад")
async def giveaway_new_back(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    current = await state.get_state()
    if current == GiveawayCreateStates.title.state:
        await state.clear()
        await message.answer("Отменено", reply_markup=admin_menu())
        return
    if current == GiveawayCreateStates.channel.state:
        await state.set_state(GiveawayCreateStates.title)
        await message.answer("Введите название розыгрыша:", reply_markup=back_only_menu())
        return
    if current == GiveawayCreateStates.rules.state:
        await state.set_state(GiveawayCreateStates.channel)
        await message.answer(
            "Введите username канала (например @channel):",
            reply_markup=back_only_menu(),
        )
        return
    if current == GiveawayCreateStates.draw_at.state:
        await state.set_state(GiveawayCreateStates.rules)
        await message.answer("Введите правила:", reply_markup=back_only_menu())
        return


@router.message(GiveawayCreateStates.title)
async def giveaway_new_title(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(GiveawayCreateStates.channel)
    await message.answer(
        "Введите username канала (например @channel):", reply_markup=back_only_menu()
    )


@router.message(GiveawayCreateStates.channel)
async def giveaway_new_channel(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    channel = normalize_channel(message.text)
    if not is_valid_channel_username(channel):
        await message.answer(
            "Неверный формат. Укажите username канала, например @channel",
            reply_markup=back_only_menu(),
        )
        return
    await state.update_data(required_channel=channel)
    await state.set_state(GiveawayCreateStates.rules)
    await message.answer("Введите правила:", reply_markup=back_only_menu())


@router.message(GiveawayCreateStates.rules)
async def giveaway_new_rules(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.update_data(rules_text=message.text.strip())
    await state.set_state(GiveawayCreateStates.draw_at)
    await message.answer(
        "Введите дату розыгрыша (ДД.ММ.ГГГГ) или '-' если нет:",
        reply_markup=back_only_menu(),
    )


@router.message(GiveawayCreateStates.draw_at)
async def giveaway_new_draw_at(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    draw_at = None
    if message.text.strip() != "-":
        try:
            draw_at = parse_date_only(message.text)
        except ValueError:
            await message.answer(
                "Неверный формат даты. Используйте ДД.ММ.ГГГГ, например 22.01.2026",
                reply_markup=back_only_menu(),
            )
            return
    await state.update_data(draw_at=draw_at)
    data = await state.get_data()
    preview = (
        f"Создать розыгрыш:\n"
        f"Название: {data['title']}\n"
        f"Канал: {data['required_channel']}\n"
        f"Дата: {data['draw_at'].strftime('%d.%m.%Y') if data['draw_at'] else '-'}\n"
        f"Правила: {data['rules_text']}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data="giveaway_create_confirm")
    kb.button(text="Отмена", callback_data="giveaway_create_cancel")
    await message.answer(preview, reply_markup=kb.as_markup())
    await state.set_state(GiveawayCreateStates.confirm)


@router.callback_query(F.data == "giveaway_create_confirm")
async def giveaway_create_confirm(callback, state: FSMContext):
    data = await state.get_data()
    payload = {
        "title": data["title"],
        "rules_text": data["rules_text"],
        "required_channel": data["required_channel"],
        "draw_at": data["draw_at"].isoformat() if data["draw_at"] else None,
    }
    async with SessionLocal() as session:
        try:
            giveaway = await create_giveaway(
                session,
                title=data["title"],
                rules_text=data["rules_text"],
                required_channel=data["required_channel"],
                draw_at=data["draw_at"],
            )
            await log_action(
                session,
                actor_tg_id=callback.from_user.id,
                action="giveaway_create",
                payload=payload,
            )
            await session.commit()
        except ActiveGiveawayExists:
            await callback.message.answer("Уже есть активный розыгрыш")
            await session.rollback()
            return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Розыгрыш создан.", reply_markup=admin_menu())
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "giveaway_create_cancel")
async def giveaway_create_cancel(callback, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Отменено", reply_markup=admin_menu())
    await callback.answer()


@router.message(Command("giveaway_edit"))
async def giveaway_edit(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await state.clear()
            await message.answer("Нет активного розыгрыша", reply_markup=admin_menu())
            return
    await state.clear()
    await state.set_state(GiveawayEditStates.choose)
    await message.answer("Что редактировать?", reply_markup=edit_menu())


@router.callback_query(F.data == "giveaway_edit_rules")
async def giveaway_edit_rules_cb(callback, state: FSMContext):
    if not await is_admin_user(callback.from_user):
        return
    await callback.answer()
    await state.update_data(edit_choice="rules")
    await state.set_state(GiveawayEditStates.rules)
    await callback.message.answer("Введите новые правила:", reply_markup=back_only_menu())


@router.callback_query(F.data == "giveaway_edit_channel")
async def giveaway_edit_channel_cb(callback, state: FSMContext):
    if not await is_admin_user(callback.from_user):
        return
    await callback.answer()
    await state.update_data(edit_choice="channel")
    await state.set_state(GiveawayEditStates.channel)
    await callback.message.answer(
        "Введите новый канал (@channel):", reply_markup=back_only_menu()
    )


@router.callback_query(F.data == "giveaway_edit_draw_at")
async def giveaway_edit_draw_at_cb(callback, state: FSMContext):
    if not await is_admin_user(callback.from_user):
        return
    await callback.answer()
    await state.update_data(edit_choice="draw_at")
    await state.set_state(GiveawayEditStates.draw_at)
    await callback.message.answer(
        "Введите новую дату (ДД.ММ.ГГГГ) или '-' если нет:",
        reply_markup=back_only_menu(),
    )


@router.message(GiveawayEditStates.choose, F.text)
async def giveaway_edit_choose(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    text = message.text.strip()
    if text == "⬅️ Назад":
        await state.clear()
        await message.answer("Отменено", reply_markup=admin_menu())
        return
    if text == "📝 Правила":
        await state.update_data(edit_choice="rules")
        await state.set_state(GiveawayEditStates.rules)
        await message.answer("Введите новые правила:", reply_markup=back_only_menu())
        return
    if text == "📣 Канал":
        await state.update_data(edit_choice="channel")
        await state.set_state(GiveawayEditStates.channel)
        await message.answer("Введите новый канал (@channel):", reply_markup=back_only_menu())
        return
    if text == "📅 Дата":
        await state.update_data(edit_choice="draw_at")
        await state.set_state(GiveawayEditStates.draw_at)
        await message.answer(
            "Введите новую дату (ДД.ММ.ГГГГ) или '-' если нет:",
            reply_markup=back_only_menu(),
        )
        return
    await message.answer("Выберите действие кнопками ниже.", reply_markup=edit_menu())


@router.message(GiveawayEditStates.rules, F.text == "⬅️ Назад")
@router.message(GiveawayEditStates.channel, F.text == "⬅️ Назад")
@router.message(GiveawayEditStates.draw_at, F.text == "⬅️ Назад")
async def giveaway_edit_back_from_field(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.set_state(GiveawayEditStates.choose)
    await message.answer("Что редактировать?", reply_markup=edit_menu())


@router.message(GiveawayEditStates.confirm, F.text == "⬅️ Назад")
async def giveaway_edit_back_from_confirm(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.set_state(GiveawayEditStates.choose)
    await message.answer("Что редактировать?", reply_markup=edit_menu())


@router.message(GiveawayEditStates.rules)
async def giveaway_edit_rules(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.update_data(rules_text=message.text.strip())
    await state.set_state(GiveawayEditStates.confirm)
    await message.answer("Подтвердить изменения?", reply_markup=_confirm_cancel_kb("giveaway_edit"))


@router.message(GiveawayEditStates.channel)
async def giveaway_edit_channel(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    channel = normalize_channel(message.text)
    if not is_valid_channel_username(channel):
        await message.answer(
            "Неверный формат. Укажите username канала, например @channel",
            reply_markup=back_only_menu(),
        )
        return
    await state.update_data(required_channel=channel)
    await state.set_state(GiveawayEditStates.confirm)
    await message.answer("Подтвердить изменения?", reply_markup=_confirm_cancel_kb("giveaway_edit"))


@router.message(GiveawayEditStates.draw_at)
async def giveaway_edit_draw_at(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    draw_at = None
    if message.text.strip() != "-":
        try:
            draw_at = parse_date_only(message.text)
        except ValueError:
            await message.answer(
                "Неверный формат даты. Используйте ДД.ММ.ГГГГ, например 22.01.2026",
                reply_markup=back_only_menu(),
            )
            return
    await state.update_data(draw_at=draw_at)
    await state.set_state(GiveawayEditStates.confirm)
    await message.answer("Подтвердить изменения?", reply_markup=_confirm_cancel_kb("giveaway_edit"))


def _confirm_cancel_kb(prefix: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data=f"{prefix}_confirm")
    kb.button(text="Назад", callback_data=f"{prefix}_back")
    kb.adjust(2)
    return kb.as_markup()


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n = n % 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


@router.callback_query(F.data == "giveaway_edit_confirm")
async def giveaway_edit_confirm(callback, state: FSMContext):
    data = await state.get_data()
    payload = {
        "rules_text": data.get("rules_text"),
        "required_channel": data.get("required_channel"),
        "draw_at": data.get("draw_at").isoformat() if data.get("draw_at") else None,
    }
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await callback.message.answer("Нет активного розыгрыша")
            return
        await update_giveaway(
            session,
            giveaway_id=giveaway.id,
            rules_text=data.get("rules_text"),
            required_channel=data.get("required_channel"),
            draw_at=data.get("draw_at"),
        )
        await log_action(
            session,
            actor_tg_id=callback.from_user.id,
            action="giveaway_edit",
            payload=payload,
        )
        await session.commit()
    # Remove inline confirm/back buttons after the action.
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.message.answer("Розыгрыш обновлен")
    await state.set_state(GiveawayEditStates.choose)
    await callback.message.answer("Что редактировать?", reply_markup=edit_menu())
    await callback.answer()


@router.callback_query(F.data == "giveaway_edit_back")
async def giveaway_edit_back(callback, state: FSMContext):
    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    if callback.message:
        await giveaway_edit(callback.message, state)
    await callback.answer()


@router.message(Command("giveaway_close"))
async def giveaway_close(message: Message):
    if not await ensure_admin(message):
        return
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer("Нет активного розыгрыша", reply_markup=admin_menu())
            return
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data="giveaway_close_confirm")
    kb.button(text="Отмена", callback_data="giveaway_close_cancel")
    await message.answer("Закрыть активный розыгрыш?", reply_markup=kb.as_markup())


@router.callback_query(F.data == "giveaway_close_confirm")
async def giveaway_close_confirm(callback):
    if not await is_admin_user(callback.from_user):
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await callback.message.answer("Нет активного розыгрыша")
            return
        await close_giveaway(session, giveaway_id=giveaway.id)
        await disable_automation(session)
        await log_action(
            session,
            actor_tg_id=callback.from_user.id,
            action="giveaway_close",
            payload={"giveaway_id": giveaway.id},
        )
        await session.commit()
    await callback.message.answer("Розыгрыш закрыт")
    await callback.answer()


@router.callback_query(F.data == "giveaway_close_cancel")
async def giveaway_close_cancel(callback):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Отменено")
    await callback.answer()


@router.message(Command("stats"))
async def stats_handler(message: Message):
    if not await ensure_admin(message):
        return
    async with SessionLocal() as session:
        users_total = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar()
        giveaways_total = (
            await session.execute(select(func.count()).select_from(Giveaway))
        ).scalar()
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await message.answer(
                f"Нет активного розыгрыша\n"
                f"Всего розыгрышей: {giveaways_total}\n"
                f"Всего пользователей: {users_total}"
            )
            return
        pending = (
            await session.execute(
                select(func.count()).select_from(Entry).where(
                    Entry.giveaway_id == giveaway.id,
                    Entry.status == EntryStatus.pending,
                )
            )
        ).scalar()
        approved = (
            await session.execute(
                select(func.count()).select_from(Entry).where(
                    Entry.giveaway_id == giveaway.id,
                    Entry.status == EntryStatus.approved,
                )
            )
        ).scalar()
        rejected = (
            await session.execute(
                select(func.count()).select_from(Entry).where(
                    Entry.giveaway_id == giveaway.id,
                    Entry.status == EntryStatus.rejected,
                )
            )
        ).scalar()
    await message.answer(
        f"Активный: {giveaway.title}\n"
        f"На проверке: {pending}, Подтверждено: {approved}, Отклонено: {rejected}\n"
        f"Всего розыгрышей: {giveaways_total}\n"
        f"Всего пользователей: {users_total}"
    )


@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.set_state(BroadcastStates.content)
    await message.answer(
        "Отправьте контент для рассылки (текст/фото/видео/док/кружок):",
        reply_markup=back_only_menu(),
    )


@router.message(BroadcastStates.content, F.text == "⬅️ Назад")
async def broadcast_content_back(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.clear()
    await message.answer("Отменено", reply_markup=admin_menu())


@router.message(BroadcastStates.content)
async def broadcast_content(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    payload_type = None
    payload_file_id = None
    text = None

    if message.text:
        payload_type = BroadcastPayloadType.text
        text = message.text
    elif message.photo:
        payload_type = BroadcastPayloadType.photo
        payload_file_id = message.photo[-1].file_id
        text = message.caption
    elif message.video:
        payload_type = BroadcastPayloadType.video
        payload_file_id = message.video.file_id
        text = message.caption
    elif message.document:
        payload_type = BroadcastPayloadType.document
        payload_file_id = message.document.file_id
        text = message.caption
    elif message.video_note:
        payload_type = BroadcastPayloadType.video_note
        payload_file_id = message.video_note.file_id
    else:
        await message.answer("Неподдерживаемый тип")
        return

    await state.update_data(
        payload_type=payload_type,
        payload_file_id=payload_file_id,
        text=text,
        segment=BroadcastSegment.all_bot_users.value,
    )
    data = await state.get_data()
    payload_labels = {
        BroadcastPayloadType.text: "Текст",
        BroadcastPayloadType.photo: "Фото",
        BroadcastPayloadType.video: "Видео",
        BroadcastPayloadType.document: "Документ",
        BroadcastPayloadType.video_note: "Кружок",
    }
    preview = (
        "Превью рассылки:\n"
        f"Тип: {payload_labels.get(data['payload_type'], data['payload_type'])}\n"
        "Сегмент: Всем пользователям в боте"
    )
    payload_type = data["payload_type"]
    if payload_type == BroadcastPayloadType.text:
        await message.answer(data["text"] or "")
    elif payload_type == BroadcastPayloadType.photo:
        await message.answer_photo(data["payload_file_id"], caption=data.get("text"))
    elif payload_type == BroadcastPayloadType.video:
        await message.answer_video(data["payload_file_id"], caption=data.get("text"))
    elif payload_type == BroadcastPayloadType.document:
        await message.answer_document(data["payload_file_id"], caption=data.get("text"))
    elif payload_type == BroadcastPayloadType.video_note:
        await message.answer_video_note(data["payload_file_id"])
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data="broadcast_confirm")
    kb.button(text="Отмена", callback_data="broadcast_cancel")
    preview_msg = await message.answer(preview, reply_markup=kb.as_markup())
    await state.update_data(
        preview_message_id=preview_msg.message_id, preview_chat_id=preview_msg.chat.id
    )
    await state.set_state(BroadcastStates.confirm)


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback, state: FSMContext):
    data = await state.get_data()
    payload = {
        "segment": data["segment"],
        "payload_type": data["payload_type"].value,
        "payload_file_id": data["payload_file_id"],
        "text": data["text"],
    }
    async with SessionLocal() as session:
        broadcast = await create_broadcast(
            session,
            created_by=callback.from_user.id,
            segment=BroadcastSegment(data["segment"]),
            payload_type=data["payload_type"],
            payload_file_id=data["payload_file_id"],
            text=data["text"],
        )
        await log_action(
            session,
            actor_tg_id=callback.from_user.id,
            action="broadcast_send",
            payload=payload,
        )
        await session.commit()
        await session.refresh(broadcast)

    celery_app.send_task("worker.tasks.send_broadcast", args=[broadcast.id])
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")
    if preview_chat_id and preview_message_id:
        try:
            await callback.message.bot.edit_message_reply_markup(
                chat_id=preview_chat_id, message_id=preview_message_id, reply_markup=None
            )
        except Exception:
            pass
    await callback.message.answer("Рассылка поставлена в очередь", reply_markup=admin_menu())
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback, state: FSMContext):
    data = await state.get_data()
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")
    if preview_chat_id and preview_message_id:
        try:
            await callback.message.bot.edit_message_reply_markup(
                chat_id=preview_chat_id, message_id=preview_message_id, reply_markup=None
            )
        except Exception:
            pass
    await state.clear()
    await callback.message.answer("Отменено", reply_markup=admin_menu())
    await callback.answer()


@router.message(Command("draw"))
async def draw_start(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await state.clear()
            await message.answer("Нет активного розыгрыша", reply_markup=admin_menu())
            return
    await state.set_state(DrawStates.count)
    await message.answer(
        "Сколько победителей выбрать? (по умолчанию 1)",
        reply_markup=back_only_menu(),
    )


@router.message(DrawStates.count, F.text == "⬅️ Назад")
async def draw_back(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    await state.clear()
    await message.answer("Отменено", reply_markup=admin_menu())


@router.message(DrawStates.count)
async def draw_count(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        return
    count = 1
    if message.text and message.text.strip().isdigit():
        count = int(message.text.strip())
    await state.update_data(count=count)
    approved_count = 0
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if giveaway:
            approved_count = (
                await session.execute(
                    select(func.count()).select_from(Entry).where(
                        Entry.giveaway_id == giveaway.id,
                        Entry.status == EntryStatus.approved,
                    )
                )
            ).scalar()
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data="draw_confirm")
    kb.button(text="Отмена", callback_data="draw_cancel")
    await message.answer(
        f"Одобренных участников: <b>{approved_count}</b>\n"
        f"Выбрать {count} {_plural_ru(count, 'победителя', 'победителя', 'победителей')}?",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(DrawStates.confirm)


@router.callback_query(F.data == "draw_confirm")
async def draw_confirm(callback, state: FSMContext):
    data = await state.get_data()
    count = data.get("count", 1)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    async with SessionLocal() as session:
        giveaway = await get_active_giveaway(session)
        if not giveaway:
            await callback.message.answer("Нет активного розыгрыша")
            return
        rows = (
            await session.execute(
                select(Entry, User)
                .join(User, User.tg_id == Entry.tg_id)
                .where(
                    Entry.giveaway_id == giveaway.id,
                    Entry.status == EntryStatus.approved,
                    User.username.is_not(None),
                )
            )
        ).all()
        entries = [row[0] for row in rows]
        users = {row[0].id: row[1] for row in rows}

        if len(entries) == 0:
            await callback.message.answer("Нет approved участников с username")
            return
        winners = random.sample(entries, k=min(count, len(entries)))
        for entry in winners:
            await create_winner(session, giveaway_id=giveaway.id, entry_id=entry.id)
        await close_giveaway(session, giveaway_id=giveaway.id)
        await disable_automation(session)
        await log_action(
            session,
            actor_tg_id=callback.from_user.id,
            action="draw_winner",
            payload={"giveaway_id": giveaway.id, "count": count},
        )
        await log_action(
            session,
            actor_tg_id=callback.from_user.id,
            action="giveaway_close_after_draw",
            payload={"giveaway_id": giveaway.id},
        )
        await session.commit()

    async with Bot(
        token=settings.admin_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as public_bot:
        winner_tg_ids = []
        for entry in winners:
            user = users[entry.id]
            username = user.username
            if not username:
                continue
            winner_tg_ids.append(user.tg_id)
            message_text = (
                "🎉🌟 ПОБЕДИТЕЛЬ РОЗЫГРЫША! 🌟🎉\n\n"
                f"🏆 Победитель: @{username}"
            )
            await public_bot.send_message(settings.public_channel, message_text)
            celery_app.send_task("worker.tasks.send_broadcast_text", args=[message_text])
            await public_bot.send_message(
                callback.from_user.id,
                f"Победитель: @{username}\nФИО: {entry.fio}\nТелефон: {entry.phone}",
            )
        consolation_text = (
            "Спасибо за участие в розыгрыше!\n"
            "В этот раз победитель уже выбран. Не расстраивайтесь — в следующий раз "
            "обязательно повезет! 🎁"
        )
        celery_app.send_task(
            "worker.tasks.send_broadcast_text_exclude",
            args=[consolation_text, winner_tg_ids],
        )

    await callback.message.answer("Победители выбраны. Розыгрыш закрыт.")
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "draw_cancel")
async def draw_cancel(callback, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Отменено")
    await callback.answer()


def run() -> None:
    setup_logging()
    bot = Bot(
        token=settings.admin_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    run()
