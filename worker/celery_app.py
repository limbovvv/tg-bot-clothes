from celery import Celery

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging

celery_app = Celery(
    "worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"],
)

setup_logging()

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
