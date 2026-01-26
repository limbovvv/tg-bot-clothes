#!/usr/bin/env bash
set -euo pipefail

alembic -c db/alembic.ini upgrade head
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
