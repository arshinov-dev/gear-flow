#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

load_env

label="${1:-manual}"
timestamp="$(date +%Y%m%d_%H%M%S)"
backup_dir="${GEARFLOW_BACKUP_DIR:-./backups}"
keep="${GEARFLOW_BACKUP_KEEP:-7}"
archive_name="gearflow_backup_${timestamp}_${label}.tar.gz"

mkdir -p "$backup_dir"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

compose up -d db >/dev/null

compose exec -T db pg_dump \
  --username="${POSTGRES_USER:-gearflow}" \
  --dbname="${POSTGRES_DB:-gearflow}" \
  --format=custom \
  > "$tmp_dir/database.dump"

compose run --rm --no-deps -T web tar -C /app -czf - media > "$tmp_dir/media.tar.gz"

cp .env "$tmp_dir/env"
cat > "$tmp_dir/metadata.txt" <<EOF
created_at=$timestamp
label=$label
app_dir=$APP_DIR
postgres_db=${POSTGRES_DB:-gearflow}
EOF

tar -C "$tmp_dir" -czf "$backup_dir/$archive_name" database.dump media.tar.gz env metadata.txt

prune_dir() {
  local dir="$1"
  find "$dir" -maxdepth 1 -name 'gearflow_backup_*.tar.gz' -type f -printf '%T@ %p\n' \
    | sort -rn \
    | awk "NR>${keep}{print \$2}" \
    | xargs -r rm --
}

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
    prune_dir "$mirror_dir"
    echo "Mirrored backup to: $mirror_dir/$(basename "$archive")"
  done
}

prune_dir "$backup_dir"
mirror_archive "$backup_dir/$archive_name"

echo "Backup created: $backup_dir/$archive_name"
