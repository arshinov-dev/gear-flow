# Разработка и отладочный запуск

Эта инструкция нужна для быстрого запуска на рабочем компьютере. Для боевого сервера используйте [DEPLOYMENT_UBUNTU_HYPERV.md](DEPLOYMENT_UBUNTU_HYPERV.md).

## Требования

- Python 3.11 или 3.12.
- Git.
- PostgreSQL не обязателен: без `POSTGRES_DB` проект использует SQLite.

## macOS

```bash
cd /path/to/gear-flow
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

Если нужно тестировать QR с телефона в той же сети:

```bash
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10:8000 \
.venv/bin/python manage.py runserver 0.0.0.0:8000 --noreload
```

## Linux

```bash
cd /path/to/gear-flow
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

Для доступа с телефона:

```bash
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10:8000 \
.venv/bin/python manage.py runserver 0.0.0.0:8000 --noreload
```

## Windows PowerShell

```powershell
cd C:\path\to\gear-flow
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8000 --noreload
```

Для доступа с телефона:

```powershell
$env:GEARFLOW_PUBLIC_BASE_URL = "http://192.168.1.10:8000"
.\.venv\Scripts\python manage.py runserver 0.0.0.0:8000 --noreload
```

## Переменные окружения

Файл `.env.example` показывает production-настройки. Django сам не читает `.env` при локальном запуске, поэтому для разработки проще задавать нужные переменные перед командой запуска.

Минимально полезные переменные:

- `GEARFLOW_PUBLIC_BASE_URL` - публичный адрес, который попадет в QR.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` - включают PostgreSQL вместо SQLite.
- `DJANGO_DEBUG=1` - режим разработки.

## Проверки перед тем, как считать задачу готовой

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test inventory
```

На Windows замените `.venv/bin/python` на `.\.venv\Scripts\python`.

## Частые проблемы

- Телефон открывает `127.0.0.1`: обновите `GEARFLOW_PUBLIC_BASE_URL` и перепечатайте QR.
- Телефон не открывает сервер: запускайте `runserver 0.0.0.0:8000`, проверьте firewall и IP компьютера.
- После изменения моделей появились новые миграции: примените `python manage.py migrate`.
- PIN не принимает вход в `/work/`: у человека должна быть роль администратора или ответственного за учет.
