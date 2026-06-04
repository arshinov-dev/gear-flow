#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

validate_env

label="$(printf '%s' "${1:-manual}" | tr -c 'A-Za-z0-9._-' '_')"
timestamp="$(date +%Y%m%d_%H%M%S)"
backup_dir="${GEARFLOW_BACKUP_DIR:-./backups}"
keep="${GEARFLOW_BACKUP_KEEP:-7}"
archive_name="gearflow_backup_${timestamp}_${label}.tar.gz"
lock_file="${GEARFLOW_BACKUP_LOCK_FILE:-/tmp/gearflow-backup.lock}"

mkdir -p "$backup_dir"
chown_to_app_owner "$backup_dir"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Backup is already running."
  exit 1
fi

compose up -d db >/dev/null

compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom' \
  > "$tmp_dir/database.dump"

compose run --rm --no-deps --entrypoint sh -T web -c 'tar -C /app -czf - media' > "$tmp_dir/media.tar.gz"

cp .env "$tmp_dir/env"
cat > "$tmp_dir/metadata.txt" <<EOF
created_at=$timestamp
label=$label
app_dir=$APP_DIR
postgres_db=${POSTGRES_DB:-gearflow}
EOF

tar -C "$tmp_dir" -czf "$backup_dir/$archive_name" database.dump media.tar.gz env metadata.txt

mirror_archive() {
  local archive="$1"
  local mirror_dirs="${GEARFLOW_BACKUP_MIRROR_DIRS:-}"
  local require_mount="${GEARFLOW_BACKUP_MIRRORS_REQUIRE_MOUNT:-1}"

  if [ -z "$mirror_dirs" ]; then
    return
  fi

  IFS=':' read -r -a dirs <<< "$mirror_dirs"
  for mirror_dir in "${dirs[@]}"; do
    if [ -z "$mirror_dir" ]; then
      continue
    fi
    if [ "$require_mount" = "1" ] && ! mountpoint -q "$mirror_dir"; then
      echo "Skip mirror, not mounted: $mirror_dir"
      continue
    fi
    mkdir -p "$mirror_dir"
    cp "$archive" "$mirror_dir/"
    prune_backups "$mirror_dir" "$keep"
    echo "Mirrored backup to: $mirror_dir/$(basename "$archive")"
  done
}

chown_to_app_owner "$backup_dir/$archive_name"
prune_backups "$backup_dir" "$keep"
mirror_archive "$backup_dir/$archive_name"

echo "Backup created: $backup_dir/$archive_name"
