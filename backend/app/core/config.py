from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    project_name: str = "Giveaway Platform"
    environment: str = "local"

    # Database
    database_url: str = "postgresql+asyncpg://app:app@postgres:5432/app"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Security / web admin
    session_secret: str = "change-me"

    # Telegram
    user_bot_token: str = ""
    admin_bot_token: str = ""
    admin_group_id: int = 0
    admin_tg_ids: str = ""
    public_channel: str = ""

    # Rate limits
    login_rate_limit: str = "5/minute"
    login_ban_max_attempts: int = 10
    login_ban_minutes: int = 30

    # Broadcast
    broadcast_rate_per_sec: int = 25


settings = Settings()
