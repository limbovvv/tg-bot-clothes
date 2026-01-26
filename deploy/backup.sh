#!/usr/bin/env bash
set -euo pipefail

TS=$(date +"%Y%m%d_%H%M%S")
OUT=${1:-"backup_${TS}.sql"}

docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-app}" "${POSTGRES_DB:-app}" > "$OUT"

echo "Backup written to $OUT"
