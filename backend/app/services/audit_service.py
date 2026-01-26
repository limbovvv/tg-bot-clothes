from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import utcnow
from backend.app.models.admin_audit_log import AdminAuditLog


async def log_action(
    session: AsyncSession, *, actor_tg_id: int, action: str, payload: dict
) -> None:
    entry = AdminAuditLog(
        actor_tg_id=actor_tg_id,
        action=action,
        payload=payload,
        created_at=utcnow(),
    )
    session.add(entry)
