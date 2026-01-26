from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.admin_login_attempt import AdminLoginAttempt


def normalize_username(username: str) -> str:
    return username.strip().lower()


async def get_login_attempt(
    session: AsyncSession, *, username: str, ip: str
) -> AdminLoginAttempt | None:
    result = await session.execute(
        select(AdminLoginAttempt).where(
            AdminLoginAttempt.username == username,
            AdminLoginAttempt.ip == ip,
        )
    )
    return result.scalar_one_or_none()


async def check_login_ban(
    session: AsyncSession, *, username: str, ip: str
) -> tuple[bool, AdminLoginAttempt | None]:
    attempt = await get_login_attempt(session, username=username, ip=ip)
    if not attempt:
        return False, None
    now = utcnow()
    if attempt.banned_until and attempt.banned_until > now:
        return True, attempt
    if attempt.banned_until and attempt.banned_until <= now:
        attempt.banned_until = None
        attempt.failed_count = 0
        attempt.first_failed_at = now
        attempt.last_failed_at = now
    return False, attempt


async def record_login_failure(
    session: AsyncSession,
    *,
    username: str,
    ip: str,
    max_attempts: int,
    ban_minutes: int,
) -> tuple[bool, AdminLoginAttempt]:
    now = utcnow()
    window = timedelta(minutes=ban_minutes)
    attempt = await get_login_attempt(session, username=username, ip=ip)
    if not attempt:
        attempt = AdminLoginAttempt(
            username=username,
            ip=ip,
            failed_count=0,
            first_failed_at=now,
            last_failed_at=now,
        )
        session.add(attempt)
    else:
        if attempt.last_failed_at < now - window:
            attempt.failed_count = 0
            attempt.first_failed_at = now
    attempt.failed_count += 1
    attempt.last_failed_at = now
    if attempt.failed_count >= max_attempts:
        attempt.banned_until = now + window
        attempt.failed_count = 0
        attempt.first_failed_at = now
        return True, attempt
    return False, attempt


async def clear_login_attempt(session: AsyncSession, *, username: str, ip: str) -> None:
    attempt = await get_login_attempt(session, username=username, ip=ip)
    if attempt:
        await session.delete(attempt)
