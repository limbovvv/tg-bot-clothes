up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose exec backend alembic -c db/alembic.ini upgrade head

backup:
	./deploy/backup.sh
