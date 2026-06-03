# Gear Flow

Веб-система учета оборудования медиацентра.

## Локальный запуск

```bash
cd /Users/maximarshinov/Documents/gear-flow
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

Админка:

```text
http://127.0.0.1:8000/admin/
```

## Первый сценарий

1. Войти в админку.
2. Создать людей в `People`: для каждого задается 4-значный PIN. Держатель `Holder` создается автоматически.
3. Открыть `Asset items`.
4. Нажать `Зарегистрировать оборудование`.
5. После регистрации открыть печатный лист QR и распечатать наклейку.
6. Сканировать QR телефоном и выполнить выдачу или возврат.

## QR URL

QR-код должен открываться обычной камерой телефона, поэтому внутри QR хранится абсолютная ссылка. В локальной сети задайте адрес системы:

```bash
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10:8000
```

На наклейке также печатается видимый `item_id`.

## PostgreSQL

Для production/dev-server окружения задайте переменные:

```bash
POSTGRES_DB=gearflow
POSTGRES_USER=gearflow
POSTGRES_PASSWORD=...
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

Если `POSTGRES_DB` не задан, Django использует локальный SQLite для разработки.

## Бэкапы

Пример ручного запуска:

```bash
. .venv/bin/activate
. .env
python manage.py backup_postgres
```

Команда требует PostgreSQL-переменные окружения и `GEARFLOW_BACKUP_DIR`.

Пример cron для ежедневного запуска в `03:00`:

```text
scripts/gearflow-backup-cron.example
```

По умолчанию хранятся 3 последних бэкапа.

## Проверки

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test inventory
```
