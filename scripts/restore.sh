#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

archive="${1:-}"
if [ -z "$archive" ] || [ ! -f "$archive" ]; then
  echo "Usage: bash scripts/restore.sh /path/to/gearflow_backup_YYYYMMDD_HHMMSS_label.tar.gz"
  exit 1
fi

load_env

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

tar -xzf "$archive" -C "$tmp_dir"

if [ "${RESTORE_ENV:-0}" = "1" ]; then
  cp "$tmp_dir/env" .env
  load_env
  compose down -v >/dev/null 2>&1 || true
fi

compose up -d db >/dev/null
compose stop nginx web >/dev/null 2>&1 || true

compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-}" db dropdb \
  --username="${POSTGRES_USER:-gearflow}" \
  --if-exists \
  "${POSTGRES_DB:-gearflow}"

compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-}" db createdb \
  --username="${POSTGRES_USER:-gearflow}" \
  "${POSTGRES_DB:-gearflow}"

compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-}" db pg_restore \
  --username="${POSTGRES_USER:-gearflow}" \
  --dbname="${POSTGRES_DB:-gearflow}" \
  < "$tmp_dir/database.dump"

compose run --rm --no-deps -T web sh -c 'rm -rf /app/media && mkdir -p /app && tar -C /app -xzf -' < "$tmp_dir/media.tar.gz"

compose run --rm web python manage.py migrate
compose run --rm web python manage.py collectstatic --noinput
compose up -d

echo "Restore complete."
