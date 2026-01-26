import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.main import create_app


@pytest.mark.asyncio
async def test_migrations_apply():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set")
    config = Config("db/alembic.ini")
    command.upgrade(config, "head")
    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
