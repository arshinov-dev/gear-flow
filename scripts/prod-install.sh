#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

install_docker() {
  if command -v docker >/dev/null 2>&1 && (docker compose version >/dev/null 2>&1 || sudo docker compose version >/dev/null 2>&1); then
    return
  fi

  sudo apt update
  sudo apt install -y ca-certificates curl python3
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  sudo apt update
  sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

write_env_if_missing() {
  if [ -f ".env" ]; then
    return
  fi

  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ip="${ip:-127.0.0.1}"

  cp .env.example .env
  python3 - "$ip" <<'PY'
import secrets
import sys
from pathlib import Path

ip = sys.argv[1]
path = Path(".env")
text = path.read_text()
replacements = {
    "DJANGO_SECRET_KEY=change-me": f"DJANGO_SECRET_KEY={secrets.token_urlsafe(48)}",
    "POSTGRES_PASSWORD=change-me": f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}",
    "DJANGO_ALLOWED_HOSTS=192.168.1.10,127.0.0.1,localhost": f"DJANGO_ALLOWED_HOSTS={ip},127.0.0.1,localhost",
    "DJANGO_CSRF_TRUSTED_ORIGINS=http://192.168.1.10": f"DJANGO_CSRF_TRUSTED_ORIGINS=http://{ip}",
    "GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10": f"GEARFLOW_PUBLIC_BASE_URL=http://{ip}",
    "GEARFLOW_BACKUP_DIR=/mnt/external/gearflow-backups": "GEARFLOW_BACKUP_DIR=./backups",
}
for old, new in replacements.items():
    text = text.replace(old, new)
path.write_text(text)
PY
fi
}

install_cron() {
  local cron_file="/etc/cron.d/gearflow-backup"
  local log_file="/var/log/gearflow-backup.log"

  sudo touch "$log_file"
  sudo tee "$cron_file" >/dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * root cd "$APP_DIR" && bash scripts/backup.sh auto >> "$log_file" 2>&1
EOF
}

install_docker
write_env_if_missing
validate_env

mkdir -p backups
compose build
compose up -d db
django_run migrate
django_run collectstatic --noinput
compose up -d

if [ "$(compose exec -T web python manage.py shell -c 'from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).exists())')" = "False" ]; then
  compose exec web python manage.py createsuperuser
fi

install_cron

echo "Gear Flow is running."
echo "Open: $(grep '^GEARFLOW_PUBLIC_BASE_URL=' .env | cut -d= -f2-)/work/"
echo "Automatic backup: every day at 03:00, cron file /etc/cron.d/gearflow-backup"
