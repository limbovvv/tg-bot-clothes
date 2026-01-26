import random
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi import HTTPException
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_session
from backend.app.models.broadcast import Broadcast
from backend.app.models.entry import Entry
from backend.app.models.enums import (
    BroadcastPayloadType,
    BroadcastSegment,
    EntryStatus,
    GiveawayStatus,
)
from backend.app.models.admin_user import AdminUser
from backend.app.models.giveaway import Giveaway
from backend.app.models.user import User
from backend.app.models.winner import Winner
from backend.app.services.audit_service import log_action
from backend.app.services.broadcast_service import create_broadcast
from backend.app.services.errors import ActiveGiveawayExists
from backend.app.services.giveaway_service import (
    close_giveaway,
    create_giveaway,
    get_active_giveaway,
    update_giveaway,
)
from backend.app.services.login_attempt_service import (
    check_login_ban,
    clear_login_attempt,
    normalize_username,
    record_login_failure,
)
from backend.app.services.winner_service import create_winner
from backend.app.web.auth import (
    clear_session,
    create_session_cookie,
    get_current_user,
    get_csrf_token,
    authenticate_admin,
    hash_password,
    login_required,
    set_session_cookie,
    verify_csrf,
)
from worker.celery_app import celery_app

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/admin", tags=["admin"])


env = Environment(
    loader=FileSystemLoader("backend/app/web/templates"),
    autoescape=select_autoescape(["html"]),
)


def format_date_only(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%d.%m.%Y")


def render(template_name: str, **context):
    template = env.get_template(template_name)
    return HTMLResponse(template.render(**context))


@router.get("/login")
async def login_page(request: Request):
    return render("login.html", request=request)


@router.post("/login")
@limiter.limit(settings.login_rate_limit)
async def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    username_clean = username.strip()
    username_key = normalize_username(username_clean)
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else get_remote_address(request)
    )

    is_banned, attempt = await check_login_ban(session, username=username_key, ip=ip)
    if is_banned and attempt and attempt.banned_until:
        await log_action(
            session,
            actor_tg_id=0,
            action="login_blocked_web",
            payload={
                "username": username_key,
                "ip": ip,
                "banned_until": attempt.banned_until.isoformat(),
            },
        )
        await session.commit()
        return render(
            "login.html",
            request=request,
            error="Слишком много попыток. Попробуйте позже.",
        )

    admin = await authenticate_admin(session, username=username_clean, password=password)
    if not admin:
        banned_now, updated_attempt = await record_login_failure(
            session,
            username=username_key,
            ip=ip,
            max_attempts=settings.login_ban_max_attempts,
            ban_minutes=settings.login_ban_minutes,
        )
        await log_action(
            session,
            actor_tg_id=0,
            action="login_failed_web",
            payload={
                "username": username_key,
                "ip": ip,
                "failed_count": updated_attempt.failed_count,
                "banned_until": (
                    updated_attempt.banned_until.isoformat()
                    if updated_attempt.banned_until
                    else None
                ),
            },
        )
        await session.commit()
        if banned_now:
            return render(
                "login.html",
                request=request,
                error="Слишком много попыток. Блокировка на 30 минут.",
            )
        return render("login.html", request=request, error="Неверные данные")

    await clear_login_attempt(session, username=username_key, ip=ip)
    await log_action(
        session,
        actor_tg_id=0,
        action="login_success_web",
        payload={"username": username_key, "ip": ip, "admin_id": admin.id},
    )
    await session.commit()

    cookie = create_session_cookie(username_clean)
    redirect = RedirectResponse(url="/admin", status_code=302)
    set_session_cookie(redirect, cookie)
    return redirect


@router.post("/logout")
async def logout_action(
    request: Request,
    response: Response,
    csrf_token: str = Form(""),
    user: str = Depends(login_required),
):
    if csrf_token:
        verify_csrf(request, csrf_token)
    clear_session(response)
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/")
async def dashboard(
    request: Request,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    users_total = (await session.execute(select(func.count()).select_from(User))).scalar()
    channel_members = None
    if settings.public_channel:
        try:
            async with Bot(
                token=settings.admin_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            ) as bot:
                channel_members = await bot.get_chat_member_count(
                    settings.public_channel
                )
        except Exception:
            channel_members = None
    giveaway = (
        await session.execute(
            select(Giveaway).where(Giveaway.status == GiveawayStatus.active)
        )
    ).scalar_one_or_none()
    pending = approved = rejected = 0
    sent_ok = sent_fail = 0
    if giveaway:
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
    sent_ok = (
        await session.execute(select(func.coalesce(func.sum(Broadcast.sent_ok), 0)))
    ).scalar()
    sent_fail = (
        await session.execute(select(func.coalesce(func.sum(Broadcast.sent_fail), 0)))
    ).scalar()
    return render(
        "dashboard.html",
        request=request,
        user=user,
        giveaway=giveaway,
        users_total=users_total,
        channel_members=channel_members,
        pending=pending,
        approved=approved,
        rejected=rejected,
        sent_ok=sent_ok,
        sent_fail=sent_fail,
        format_date_only=format_date_only,
        csrf=get_csrf_token(request),
    )


@router.get("/admins")
async def admins_list(
    request: Request,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(select(AdminUser).order_by(AdminUser.created_at.desc()))
    ).scalars().all()
    return render(
        "admins.html",
        request=request,
        user=user,
        admins=rows,
        csrf=get_csrf_token(request),
    )


@router.get("/users")
async def users_list(
    request: Request,
    q: str | None = None,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    query = select(User).order_by(User.first_seen_at.desc())
    if q:
        like = f"%{q}%"
        query = query.where(
            User.username.ilike(like) | cast(User.tg_id, String).ilike(like)
        )
    rows = (await session.execute(query)).scalars().all()
    return render(
        "users.html",
        request=request,
        user=user,
        title="Пользователи бота",
        users=rows,
        q=q or "",
        csrf=get_csrf_token(request),
    )


@router.get("/channel-users")
async def channel_users_list(
    request: Request,
    user: str = Depends(login_required),
):
    raise HTTPException(status_code=404)


@router.post("/admins/create")
async def admins_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    admin = AdminUser(
        username=username.strip(),
        password_hash=hash_password(password),
        is_active=True,
        created_at=datetime.utcnow(),
    )
    session.add(admin)
    await log_action(
        session,
        actor_tg_id=0,
        action="admin_create_web",
        payload={"username": admin.username},
    )
    await session.commit()
    return RedirectResponse(url="/admin/admins", status_code=302)


@router.post("/admins/{admin_id}/toggle")
async def admins_toggle(
    request: Request,
    admin_id: int,
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    admin = await session.get(AdminUser, admin_id)
    if admin:
        admin.is_active = not admin.is_active
        await log_action(
            session,
            actor_tg_id=0,
            action="admin_toggle_web",
            payload={"admin_id": admin_id, "is_active": admin.is_active},
        )
        await session.commit()
    return RedirectResponse(url="/admin/admins", status_code=302)


@router.post("/admins/{admin_id}/reset")
async def admins_reset(
    request: Request,
    admin_id: int,
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    admin = await session.get(AdminUser, admin_id)
    if admin:
        admin.password_hash = hash_password(new_password)
        await log_action(
            session,
            actor_tg_id=0,
            action="admin_reset_password_web",
            payload={"admin_id": admin_id},
        )
        await session.commit()
    return RedirectResponse(url="/admin/admins", status_code=302)


@router.post("/admins/{admin_id}/reset-default")
async def admins_reset_default(
    request: Request,
    admin_id: int,
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    admin = await session.get(AdminUser, admin_id)
    if admin:
        admin.password_hash = hash_password("12345")
        await log_action(
            session,
            actor_tg_id=0,
            action="admin_reset_default_web",
            payload={"admin_id": admin_id},
        )
    await session.commit()
    return RedirectResponse(url="/admin/admins", status_code=302)


@router.post("/admins/{admin_id}/delete")
async def admins_delete(
    request: Request,
    admin_id: int,
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    admin = await session.get(AdminUser, admin_id)
    if admin:
        await log_action(
            session,
            actor_tg_id=0,
            action="admin_delete_web",
            payload={"admin_id": admin_id, "username": admin.username},
        )
        await session.delete(admin)
        await session.commit()
    return RedirectResponse(url="/admin/admins", status_code=302)


@router.get("/entries")
async def entries_list(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    giveaway = await get_active_giveaway(session)
    if not giveaway:
        return render(
            "entries.html",
            request=request,
            user=user,
            entries=[],
            users={},
            csrf=get_csrf_token(request),
        )
    query = (
        select(Entry, User)
        .join(User, User.tg_id == Entry.tg_id)
        .where(Entry.giveaway_id == giveaway.id)
        .order_by(Entry.created_at.desc())
    )
    if status:
        query = query.where(Entry.status == status)
    if q:
        like = f"%{q}%"
        query = query.where(
            Entry.fio.ilike(like)
            | Entry.phone.ilike(like)
            | User.username.ilike(like)
        )

    rows = (await session.execute(query)).all()
    return render(
        "entries.html",
        request=request,
        user=user,
        entries=[row[0] for row in rows],
        users={row[0].id: row[1] for row in rows},
        csrf=get_csrf_token(request),
    )


@router.post("/entries/{entry_id}/approve")
async def approve_entry(
    request: Request,
    entry_id: int,
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    entry = await session.get(Entry, entry_id)
    if entry:
        entry.status = EntryStatus.approved
        await log_action(
            session,
            actor_tg_id=0,
            action="entry_approve_web",
            payload={"entry_id": entry_id},
        )
    await session.commit()
    return RedirectResponse(url="/admin/entries", status_code=302)


@router.post("/entries/{entry_id}/reject")
async def reject_entry(
    request: Request,
    entry_id: int,
    reason: str = Form(""),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    entry = await session.get(Entry, entry_id)
    if entry:
        entry.status = EntryStatus.rejected
        entry.reject_reason_text = reason or None
        await log_action(
            session,
            actor_tg_id=0,
            action="entry_reject_web",
            payload={"entry_id": entry_id, "reason": reason or None},
        )
    await session.commit()
    return RedirectResponse(url="/admin/entries", status_code=302)


@router.get("/giveaway")
async def giveaway_view(
    request: Request,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    giveaway = await get_active_giveaway(session)
    return render(
        "giveaway.html",
        request=request,
        user=user,
        giveaway=giveaway,
        format_date_only=format_date_only,
        csrf=get_csrf_token(request),
    )


@router.post("/giveaway/create")
async def giveaway_create(
    request: Request,
    title: str = Form(...),
    required_channel: str = Form(...),
    rules_text: str = Form(...),
    draw_at: str = Form(""),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    try:
        draw_dt = datetime.strptime(draw_at, "%d.%m.%Y") if draw_at else None
    except ValueError:
        return RedirectResponse(url="/admin/giveaway", status_code=302)
    try:
        await create_giveaway(
            session,
            title=title,
            rules_text=rules_text,
            required_channel=required_channel,
            draw_at=draw_dt,
        )
        await log_action(
            session,
            actor_tg_id=0,
            action="giveaway_create_web",
            payload={
                "title": title,
                "required_channel": required_channel,
                "rules_text": rules_text,
                "draw_at": draw_dt.isoformat() if draw_dt else None,
            },
        )
        await session.commit()
    except ActiveGiveawayExists:
        await session.rollback()
    return RedirectResponse(url="/admin/giveaway", status_code=302)


@router.post("/giveaway/update")
async def giveaway_update(
    request: Request,
    giveaway_id: int = Form(...),
    required_channel: str = Form(...),
    rules_text: str = Form(...),
    draw_at: str = Form(""),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    try:
        draw_dt = datetime.strptime(draw_at, "%d.%m.%Y") if draw_at else None
    except ValueError:
        return RedirectResponse(url="/admin/giveaway", status_code=302)
    await update_giveaway(
        session,
        giveaway_id=giveaway_id,
        rules_text=rules_text,
        required_channel=required_channel,
        draw_at=draw_dt,
    )
    await log_action(
        session,
        actor_tg_id=0,
        action="giveaway_update_web",
        payload={
            "giveaway_id": giveaway_id,
            "required_channel": required_channel,
            "rules_text": rules_text,
            "draw_at": draw_dt.isoformat() if draw_dt else None,
        },
    )
    await session.commit()
    return RedirectResponse(url="/admin/giveaway", status_code=302)


@router.post("/giveaway/close")
async def giveaway_close(
    request: Request,
    giveaway_id: int = Form(...),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    verify_csrf(request, csrf_token)
    await close_giveaway(session, giveaway_id=giveaway_id)
    await log_action(
        session,
        actor_tg_id=0,
        action="giveaway_close_web",
        payload={"giveaway_id": giveaway_id},
    )
    await session.commit()
    return RedirectResponse(url="/admin/giveaway", status_code=302)


@router.get("/winners")
async def winners_view(
    request: Request,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    raise HTTPException(status_code=404)
    rows = (
        await session.execute(
            select(Winner, Entry, User)
            .join(Entry, Entry.id == Winner.entry_id)
            .join(User, User.tg_id == Entry.tg_id)
            .order_by(Winner.chosen_at.desc())
        )
    ).all()
    return render(
        "winners.html",
        request=request,
        user=user,
        winners=[row[0] for row in rows],
        entries={row[1].id: row[1] for row in rows},
        users={row[1].id: row[2] for row in rows},
        csrf=get_csrf_token(request),
    )


@router.post("/winners/draw")
async def winners_draw(
    request: Request,
    count: int = Form(1),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    raise HTTPException(status_code=404)
    verify_csrf(request, csrf_token)
    giveaway = await get_active_giveaway(session)
    if not giveaway:
        return RedirectResponse(url="/admin/winners", status_code=302)
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
    if not entries:
        return RedirectResponse(url="/admin/winners", status_code=302)
    winners = random.sample(entries, k=min(count, len(entries)))
    for entry in winners:
        await create_winner(session, giveaway_id=giveaway.id, entry_id=entry.id)
    await log_action(
        session,
        actor_tg_id=0,
        action="draw_winner_web",
        payload={"giveaway_id": giveaway.id, "count": count},
    )
    await session.commit()

    async with Bot(
        token=settings.admin_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as public_bot:
        for entry in winners:
            user_data = users[entry.id]
            username = user_data.username
            if not username:
                continue
            message_text = f"Победитель: @{username}"
            await public_bot.send_message(settings.public_channel, message_text)
            celery_app.send_task("worker.tasks.send_broadcast_text", args=[message_text])
    return RedirectResponse(url="/admin/winners", status_code=302)


@router.get("/broadcasts")
async def broadcasts_view(
    request: Request,
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    raise HTTPException(status_code=404)


@router.post("/broadcasts/create")
async def broadcasts_create(
    request: Request,
    segment: str = Form(...),
    payload_type: str = Form(...),
    payload_file_id: str = Form(""),
    text: str = Form(""),
    csrf_token: str = Form(...),
    user: str = Depends(login_required),
    session: AsyncSession = Depends(get_session),
):
    raise HTTPException(status_code=404)
