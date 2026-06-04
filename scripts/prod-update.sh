#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

load_env

bash scripts/backup.sh before-update

compose pull --ignore-buildable || true

if [ -d ".git" ]; then
  git pull --ff-only
else
  echo "No .git directory found; using current files."
fi

compose build
compose up -d db
compose run --rm web python manage.py migrate
compose run --rm web python manage.py collectstatic --noinput
compose up -d
compose ps

echo "Update complete."
