#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$APP_DIR"

compose() {
  if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    sudo docker compose "$@"
  fi
}

load_env() {
  if [ -f "$APP_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$APP_DIR/.env"
    set +a
  fi
}

require_env_file() {
  if [ ! -f "$APP_DIR/.env" ]; then
    echo "No .env file found. Run bash scripts/prod-install.sh first."
    exit 1
  fi
}

validate_env() {
  require_env_file
  load_env
  if [ "${DJANGO_SECRET_KEY:-change-me}" = "change-me" ] || [ "${POSTGRES_PASSWORD:-change-me}" = "change-me" ]; then
    echo ".env still contains change-me secrets. Run bash scripts/prod-install.sh or edit .env."
    exit 1
  fi
}

django_run() {
  compose run --rm web python manage.py "$@"
}

random_value() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

host_ip() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

prune_backups() {
  local dir="$1"
  local keep="$2"

  python3 - "$dir" "$keep" <<'PY'
import sys
from pathlib import Path

directory = Path(sys.argv[1])
keep = int(sys.argv[2])
files = sorted(
    directory.glob("gearflow_backup_*.tar.gz"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
for path in files[keep:]:
    path.unlink()
PY
}

chown_to_app_owner() {
  local path="$1"
  if [ "$(id -u)" != "0" ]; then
    return
  fi

  local owner
  owner="$(stat -c '%u:%g' "$APP_DIR" 2>/dev/null || true)"
  if [ -n "$owner" ]; then
    chown "$owner" "$path" 2>/dev/null || true
  fi
}
