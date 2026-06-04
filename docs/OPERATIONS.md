# Gear Flow: рабочие сценарии

Это короткая инструкция под реальные действия: быстро запустить разработку, быстро поставить на Ubuntu, обновить сервер и восстановиться из бэкапа.

## 1. Продолжение разработки: быстро запустить и посмотреть

На Mac/Linux:

```bash
bash scripts/dev.sh
```

Открыть:

```text
http://127.0.0.1:8000/work/
```

Если нужно открыть с телефона в той же сети:

```bash
GEARFLOW_DEV_BIND=0.0.0.0:8000 \
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10:8000 \
bash scripts/dev.sh
```

Что делает `dev.sh`:

- создает `.venv`, если его нет;
- ставит зависимости;
- применяет миграции;
- запускает Django dev-server.

Для обычной разработки Docker не обязателен: быстрее крутить локальный dev-server и просто смотреть интерфейс.

## 2. Быстрая развертка на чистой Ubuntu

Если код доступен через Git, на новой Ubuntu можно выполнить одной пачкой:

```bash
sudo apt update && sudo apt install -y git && sudo git clone <repo-url> /opt/gear-flow && sudo chown -R "$USER":"$USER" /opt/gear-flow && cd /opt/gear-flow && bash scripts/prod-install.sh
```

Если код уже скопирован на сервер:

```bash
cd /opt/gear-flow
bash scripts/prod-install.sh
```

Что делает `prod-install.sh`:

- ставит Docker Engine и Docker Compose plugin, если их нет;
- создает `.env`, генерирует секреты и пароль PostgreSQL;
- определяет IP Ubuntu VM и подставляет его в `GEARFLOW_PUBLIC_BASE_URL`;
- поднимает `postgres + web + nginx`;
- применяет миграции;
- собирает статику;
- предлагает создать Django superuser;
- ставит автоматический backup каждый день в `03:00`.

После установки:

```text
http://IP_UBUNTU/work/
http://IP_UBUNTU/admin/
```

## 3. Обновление Ubuntu после разработки

На сервере:

```bash
cd /opt/gear-flow
bash scripts/prod-update.sh
```

Что делает `prod-update.sh`:

- сначала создает backup;
- делает `git pull --ff-only`, если проект Git-репозиторий;
- пересобирает Docker image;
- применяет миграции;
- собирает статику;
- перезапускает контейнеры.

То есть обычный порядок после завершения разработки такой:

```text
закоммитил изменения -> на Ubuntu запустил bash scripts/prod-update.sh
```

## 4. Бэкапы: ручной и автоматический

Ручной backup:

```bash
cd /opt/gear-flow
bash scripts/backup.sh manual
```

Автоматический backup ставится командой `prod-install.sh`:

```text
/etc/cron.d/gearflow-backup
```

Расписание по умолчанию:

```text
каждый день в 03:00
```

Backup-файл один:

```text
gearflow_backup_YYYYMMDD_HHMMSS_manual.tar.gz
```

Внутри:

- PostgreSQL dump;
- `media/`;
- `.env`;
- metadata.

## 5. Три места хранения backup

Основной backup на Ubuntu задается в `.env`:

```bash
GEARFLOW_BACKUP_DIR=./backups
```

Дополнительные зеркала задаются через `:`:

```bash
GEARFLOW_BACKUP_MIRROR_DIRS=/mnt/windows-backups:/mnt/usb-backups
GEARFLOW_BACKUP_MIRRORS_REQUIRE_MOUNT=1
```

Практичный вариант для Hyper-V:

- `./backups` - копия внутри Ubuntu VM;
- `/mnt/windows-backups` - папка Windows-хоста, примонтированная в Ubuntu;
- флешки - копировать с Windows из этой папки на флешку, либо примонтировать флешку в Ubuntu как `/mnt/usb-backups`.

`GEARFLOW_BACKUP_MIRRORS_REQUIRE_MOUNT=1` защищает от ошибки: если Windows-папка или флешка не примонтирована, скрипт пропустит зеркало и не забьет диск Ubuntu.

## 6. Восстановление после потери сервера

На новой Ubuntu:

```bash
sudo apt update && sudo apt install -y git && sudo git clone <repo-url> /opt/gear-flow && sudo chown -R "$USER":"$USER" /opt/gear-flow && cd /opt/gear-flow && bash scripts/prod-install.sh
```

Потом восстановить backup:

```bash
cd /opt/gear-flow
RESTORE_ENV=1 bash scripts/restore.sh /path/to/gearflow_backup_YYYYMMDD_HHMMSS_manual.tar.gz
```

`RESTORE_ENV=1` означает: взять `.env` из backup-архива. Для восстановления после полной потери сервера это обычно правильно.

Важно: при `RESTORE_ENV=1` скрипт пересоздает Docker volumes перед восстановлением. Используйте этот режим для новой VM или полного восстановления, а не для просмотра архива на живом сервере.

## 7. Почему Docker тут проще

Docker убирает большую часть ручной настройки:

- PostgreSQL не надо ставить и настраивать отдельно;
- Gunicorn и Nginx поднимаются одинаково на любой Ubuntu;
- обновление сводится к одному скрипту;
- восстановление новой VM становится повторяемым;
- база и файлы лежат в Docker volumes, а backup делается через `pg_dump` и архив `media`.

Важно: Docker не заменяет backup. Volume может потеряться вместе с VM, поэтому `backup.sh` обязателен.

## 8. Как устроен Docker в проекте

- `compose.yaml` - три сервиса: `db`, `web`, `nginx`.
- `Dockerfile` - Python-приложение под Gunicorn, запускается не от root.
- `docker/nginx.conf` - Nginx отдает `/static/`, `/media/` и проксирует Django.
- `docker/entrypoint.sh` - ждет PostgreSQL перед запуском приложения.
- `postgres_data`, `media_data`, `static_data` - Docker volumes с постоянными данными.

Миграции не спрятаны внутрь автозапуска контейнера. Их явно выполняют `prod-install.sh`, `prod-update.sh` и `restore.sh`, чтобы обновление было управляемым.

## Полезные команды

Статус:

```bash
docker compose ps
```

Логи:

```bash
docker compose logs -f web
docker compose logs -f nginx
docker compose logs -f db
```

Остановить:

```bash
docker compose down
```

Поднять:

```bash
docker compose up -d
```

## Источники

- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- Docker Compose env files: https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/
- Docker Compose `.env`: https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/
- PostgreSQL official Docker image: https://hub.docker.com/_/postgres
