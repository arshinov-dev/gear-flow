#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

check_file_exists() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Missing file: $path"
    exit 1
  fi
}

check_shell() {
  local path="$1"
  bash -n "$path"
  echo "OK shell: $path"
}

required_files=(
  Dockerfile
  compose.yaml
  docker/entrypoint.sh
  docker/nginx.conf
  scripts/_lib.sh
  scripts/dev.sh
  scripts/prod-install.sh
  scripts/prod-update.sh
  scripts/backup.sh
  scripts/restore.sh
)

for path in "${required_files[@]}"; do
  check_file_exists "$path"
done

for path in \
  scripts/_lib.sh \
  scripts/dev.sh \
  scripts/prod-install.sh \
  scripts/prod-update.sh \
  scripts/backup.sh \
  scripts/restore.sh \
  scripts/doctor.sh \
  docker/entrypoint.sh; do
  check_shell "$path"
done

if [ -f ".env" ]; then
  if grep -q "change-me" .env; then
    echo ".env contains change-me. Edit it or rerun prod-install on a fresh .env."
    exit 1
  fi
  echo "OK env: .env"
else
  echo "WARN env: .env is missing; prod-install.sh will create it."
fi

if command -v docker >/dev/null 2>&1 && (docker info >/dev/null 2>&1 || sudo -n docker info >/dev/null 2>&1); then
  if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose config >/dev/null
  else
    sudo docker compose config >/dev/null
  fi
  echo "OK docker compose config"
else
  echo "WARN docker: Docker is not installed or needs sudo password; skipping compose config."
fi

if [ -x ".venv/bin/python" ]; then
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run
  echo "OK django checks"
else
  echo "WARN django: .venv not found; skipping local Django checks."
fi

echo "Doctor finished."
