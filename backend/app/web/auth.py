import secrets

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext

from backend.app.core.config import settings
from backend.app.db.session import get_session
from backend.app.models.admin_user import AdminUser
from backend.app.core.time import utcnow

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="admin-session")


def verify_password(plain: str, password_hash: str) -> bool:
    return pwd_context.verify(plain, password_hash)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_session_cookie(username: str) -> str:
    serializer = get_serializer()
    return serializer.dumps({"u": username, "csrf": secrets.token_urlsafe(16)})


def clear_session(response: Response) -> None:
    response.delete_cookie("session")


def set_session_cookie(response: Response, value: str) -> None:
    secure = settings.environment.lower() not in {"local", "dev", "development"}
    response.set_cookie(
        "session",
        value,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=60 * 60 * 12,
    )


def get_session_data(request: Request) -> dict:
    cookie = request.cookies.get("session")
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    serializer = get_serializer()
    try:
        data = serializer.loads(cookie, max_age=60 * 60 * 12)
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    return data


def get_current_user(request: Request) -> str:
    data = get_session_data(request)
    return data.get("u")


def get_csrf_token(request: Request) -> str:
    data = get_session_data(request)
    token = data.get("csrf")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return token


def verify_csrf(request: Request, token: str) -> None:
    if token != get_csrf_token(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def login_required(user: str = Depends(get_current_user)) -> str:
    return user


async def authenticate_admin(
    session: AsyncSession, *, username: str, password: str
) -> AdminUser | None:
    result = await session.execute(
        select(AdminUser).where(AdminUser.username == username, AdminUser.is_active.is_(True))
    )
    admin = result.scalar_one_or_none()
    if not admin:
        return None
    if not verify_password(password, admin.password_hash):
        return None
    admin.last_login_at = utcnow()
    return admin
