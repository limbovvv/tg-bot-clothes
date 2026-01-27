# Платформа розыгрышей в Telegram

Production‑ready репозиторий: user‑bot, admin‑bot, FastAPI backend, веб‑админка, PostgreSQL, Celery, Redis, nginx, docker‑compose/podman‑compose. Почему podman? У меня не работал докер.

## Стек
- Python 3.12
- aiogram v3 (FSM)
- FastAPI + Uvicorn
- PostgreSQL + Alembic
- Celery + Redis
- nginx

## Структура проекта
- `backend/` — FastAPI приложение + веб‑админка
- `bots/user_bot/` — пользовательский бот
- `bots/admin_bot/` — админ‑бот
- `worker/` — Celery воркер + планировщик (beat)
- `db/` — Alembic миграции
- `deploy/` — nginx + скрипт бэкапа
- `scripts/` — утилиты

## Быстрый запуск (локально)
1) Скопировать env:
```bash
cp .env.example .env
```
2) Заполнить `.env` (токены, канал, группа, секреты).
3) Запуск:
```bash
podman-compose up -d --build
```
4) Применить миграции:
```bash
podman-compose exec backend alembic -c db/alembic.ini upgrade head
```
5) Открыть админку:
- напрямую: `http://localhost:8000/admin`
- через nginx: `http://localhost:8080/admin`

## Создание админа для веб‑панели
```bash
podman-compose exec backend python /app/scripts/create_admin.py \
  --username YOUR_LOGIN --password YOUR_PASSWORD
```
Логин — **без @**.

## Переменные окружения
Полный список — в `.env.example`.
Критично заполнить:
- `USER_BOT_TOKEN`
- `ADMIN_BOT_TOKEN`
- `ADMIN_GROUP_ID`
- `ADMIN_TG_IDS`
- `PUBLIC_CHANNEL`
- `SESSION_SECRET`

## Настройка Telegram
- **User‑bot** должен быть админом в публичном канале (для проверки подписки).
- **User‑bot** должен быть добавлен в админ‑группу (чтобы постить заявки на модерацию).
- **Admin‑bot** администрирует канал/группу, где нужны уведомления.

## Веб‑админка
Разделы: Dashboard, Заявки, Розыгрыш, Пользователи бота, Админы.
Мобильное меню — через выезжающую боковую панель (offcanvas).

## Рассылки
- Отправка выполняется через Celery.
- Скорость регулируется `BROADCAST_RATE_PER_SEC`.

## Автоматический ежемесячный розыгрыш
- Настраивается в разделе `Розыгрыш` → блок «Автоматический розыгрыш».
- Планировщик Celery Beat проверяет настройки каждый день в 00:05 UTC.
- В день месяца из настроек:
  1) активный розыгрыш закрывается,
  2) создаётся новый по шаблону.
- Если закрыть розыгрыш вручную, автоматический режим отключается.

## Миграции
```bash
podman-compose exec backend alembic -c db/alembic.ini upgrade head
```

## Бэкап PostgreSQL
```bash
./deploy/backup.sh
```
Файл будет в текущей папке.

## Обновление на сервере
```bash
cd ~/tg-bot-clothes
git pull
podman-compose up -d --build
podman-compose exec backend alembic -c db/alembic.ini upgrade head
```

Если нужно гарантированно пересоздать сервисы после изменений:
```bash
podman stop tg-bot-clothes_backend_1 tg-bot-clothes_worker_1 tg-bot-clothes_beat_1 tg-bot-clothes_admin_bot_1
podman rm tg-bot-clothes_backend_1 tg-bot-clothes_worker_1 tg-bot-clothes_beat_1 tg-bot-clothes_admin_bot_1
podman-compose up -d --build backend worker beat admin_bot
```

Если обновлялась только веб‑часть, можно пересобрать только backend:
```bash
podman-compose up -d --build backend
```

Если нужно, чтобы контейнер backend точно обновился:
```bash
podman stop tg-bot-clothes_backend_1
podman rm tg-bot-clothes_backend_1
podman-compose up -d backend
```

## HTTPS (nginx + certbot)
Пример для сервера:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Troubleshooting
- Логи: `podman-compose logs -f --tail=200`
- Проверка статуса: `podman-compose ps`
- Если не открывается админка — проверь, что открыт порт 8080 в firewall/security‑group.

## Тесты
```bash
pytest -q
```
Тест `test_migrations_apply` требует доступную БД.
