#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/gear-flow"

cd "$APP_DIR"

set -a
source .env
set +a

: "${GEARFLOW_BACKUP_DIR:?Set GEARFLOW_BACKUP_DIR in .env}"

mkdir -p "$GEARFLOW_BACKUP_DIR"
mkdir -p media

source .venv/bin/activate
python manage.py backup_postgres

timestamp="$(date +%Y%m%d_%H%M%S)"
tar --create --gzip --file "$GEARFLOW_BACKUP_DIR/gearflow_media_${timestamp}.tar.gz" media .env

keep="${GEARFLOW_BACKUP_KEEP:-7}"
find "$GEARFLOW_BACKUP_DIR" -name "gearflow_media_*.tar.gz" -type f -printf "%T@ %p\n" \
  | sort -rn \
  | awk "NR>${keep}{print \$2}" \
  | xargs -r rm --
