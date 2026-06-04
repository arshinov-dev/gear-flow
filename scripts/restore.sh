#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

archive="${1:-}"
if [ -z "$archive" ] || [ ! -f "$archive" ]; then
  echo "Usage: bash scripts/restore.sh /path/to/gearflow_backup_YYYYMMDD_HHMMSS_label.tar.gz"
  exit 1
fi

if [ "${RESTORE_ENV:-0}" != "1" ]; then
  validate_env
else
  load_env
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

tar -xzf "$archive" -C "$tmp_dir"

if [ "${RESTORE_ENV:-0}" = "1" ]; then
  cp "$tmp_dir/env" .env
  validate_env
  compose down -v >/dev/null 2>&1 || true
fi

compose up -d db >/dev/null
compose stop nginx web >/dev/null 2>&1 || true

compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" dropdb --host=127.0.0.1 --username="$POSTGRES_USER" --if-exists "$POSTGRES_DB"'

compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" createdb --host=127.0.0.1 --username="$POSTGRES_USER" "$POSTGRES_DB"'

compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  < "$tmp_dir/database.dump"

compose run --rm --no-deps --entrypoint sh -T web -c 'mkdir -p /app/media && find /app/media -mindepth 1 -depth -exec rm -rf {} + && tar -C /app -xzf -' < "$tmp_dir/media.tar.gz"

django_run migrate
django_run collectstatic --noinput
compose up -d

echo "Restore complete."
