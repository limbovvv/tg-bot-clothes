# Telegram Giveaway Platform

Production-ready monorepo: user bot, admin bot, FastAPI backend, admin web, PostgreSQL, Celery worker, Redis, nginx, docker-compose.

## Stack
- Python 3.12
- aiogram v3 (FSM)
- FastAPI + Uvicorn
- PostgreSQL + Alembic
- Celery + Redis
- nginx

## Repository layout
- `backend/` FastAPI app (admin web)
- `bots/user_bot/` User bot
- `bots/admin_bot/` Admin bot
- `worker/` Celery worker
- `db/` Alembic migrations
- `deploy/` nginx + backup script
- `scripts/` helper scripts

## Quick start (local)
1. Copy env:
   ```bash
   cp .env.example .env
   ```
2. Fill required values in `.env` (tokens, admin ids, channel).
3. Build and run:
   ```bash
   make up
   ```
4. Open admin web:
   - http://localhost:8000/admin (direct)
   - http://localhost/ (via nginx)

## Admin users
Create admin in DB (after services are up and migrations applied):
```bash
podman exec -it tgbotclothes_backend_1 python scripts/create_admin.py --username admin --password CHANGE_ME
```

## ENV variables
See `.env.example` for full list. Required:
- `USER_BOT_TOKEN`
- `ADMIN_BOT_TOKEN`
- `ADMIN_GROUP_ID`
- `ADMIN_TG_IDS`
- `PUBLIC_CHANNEL`
- `SESSION_SECRET`

## Bots setup
- Add **user bot** to the admin group and grant permission to send messages.
- Add **admin bot** to the public channel as admin.
- Ensure the required channel is public and the user bot is admin in it for membership checks.

## Admin web
- Login at `/admin/login`.
- Sections: Dashboard, Entries, Giveaway, Winners, Broadcasts.

## Broadcasts
- All broadcasts are processed by Celery worker.
- Rate limit is controlled by `BROADCAST_RATE_PER_SEC`.

## Draw winners
- Winners are chosen only among approved entries with username.
- Public post format: `Winner: @username`.
- Broadcast to all bot users is triggered automatically.

## Migrations
Run manually:
```bash
make migrate
```

## Backup
```bash
make backup
```
Output file is written to current directory.

## Update / deploy
- Pull new code, update `.env` if needed, then:
  ```bash
  make up
  ```

## HTTPS (nginx + certbot)
Example steps on server:
```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```
Update `deploy/nginx.conf` with your domain and reload nginx.

## Troubleshooting
- Check logs: `make logs`
- Ensure bots are admins where required.
- Verify DB/Redis health in docker compose.

## Tests
```bash
pytest -q
```
`test_migrations_apply` requires `DATABASE_URL` to point to a running Postgres.
