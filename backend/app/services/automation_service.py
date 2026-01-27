from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.giveaway_automation import GiveawayAutomationSettings


async def get_automation_settings(session: AsyncSession) -> GiveawayAutomationSettings:
    settings = await session.get(GiveawayAutomationSettings, 1)
    if settings:
        return settings
    settings = GiveawayAutomationSettings(
        id=1,
        is_enabled=False,
        day_of_month=1,
        title_template="Ежемесячный розыгрыш",
        rules_text="",
        required_channel="",
        draw_offset_days=0,
        updated_at=utcnow(),
    )
    session.add(settings)
    await session.flush()
    return settings


async def update_automation_settings(
    session: AsyncSession,
    *,
    is_enabled: bool,
    day_of_month: int,
    title_template: str,
    rules_text: str,
    required_channel: str,
    draw_offset_days: int,
) -> GiveawayAutomationSettings:
    settings = await get_automation_settings(session)
    settings.is_enabled = is_enabled
    settings.day_of_month = max(1, min(day_of_month, 28))
    settings.title_template = title_template.strip() or settings.title_template
    settings.rules_text = rules_text.strip()
    settings.required_channel = required_channel.strip()
    settings.draw_offset_days = max(0, min(draw_offset_days, 31))
    settings.updated_at = utcnow()
    return settings


async def disable_automation(session: AsyncSession) -> GiveawayAutomationSettings:
    settings = await get_automation_settings(session)
    if settings.is_enabled:
        settings.is_enabled = False
        settings.updated_at = utcnow()
    return settings


async def should_run_for_month(settings: GiveawayAutomationSettings, now: datetime) -> bool:
    current_month = now.strftime("%Y-%m")
    return settings.last_run_month != current_month


async def mark_run_month(
    session: AsyncSession, settings: GiveawayAutomationSettings, now: datetime
) -> None:
    settings.last_run_month = now.strftime("%Y-%m")
    settings.updated_at = utcnow()
    await session.flush()
