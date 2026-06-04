#!/usr/bin/env sh
set -eu

if [ "${GEARFLOW_WAIT_FOR_DB:-0}" = "1" ]; then
  until pg_isready \
    --host="${POSTGRES_HOST:-db}" \
    --port="${POSTGRES_PORT:-5432}" \
    --username="${POSTGRES_USER:-gearflow}" \
    --dbname="${POSTGRES_DB:-gearflow}" >/dev/null 2>&1; do
    sleep 1
  done
fi

exec "$@"
