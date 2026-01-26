#!/usr/bin/env python
import argparse
import asyncio

from passlib.context import CryptContext
from sqlalchemy import select

from backend.app.core.time import utcnow
from backend.app.db.session import SessionLocal
from backend.app.models.admin_user import AdminUser

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create admin user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    password_hash = pwd_context.hash(args.password)
    async with SessionLocal() as session:
        existing = await session.execute(
            select(AdminUser).where(AdminUser.username == args.username)
        )
        if existing.scalar_one_or_none():
            raise SystemExit("Admin already exists")
        admin = AdminUser(
            username=args.username,
            password_hash=password_hash,
            is_active=True,
            created_at=utcnow(),
        )
        session.add(admin)
        await session.commit()
    print("Admin created")


if __name__ == "__main__":
    asyncio.run(main())
