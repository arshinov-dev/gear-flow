# Gear Flow

Gear Flow - веб-система учета оборудования, комплектов и мерча для медиацентра.

## Что внутри

- QR-выдача и возврат оборудования.
- Выдача комплектов одним QR, например стойка + свет + кабель.
- Рабочий кабинет `/work/` для ответственных за учет.
- Аудит мест хранения и экран просрочек.
- Учет мерча без QR: остатки, поступления, перемещения, выдачи и корректировки.
- Django admin для настройки справочников, людей, ролей, мест, оборудования и SKU.

## Быстрый локальный запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

Адреса:

- рабочий кабинет: `http://127.0.0.1:8000/work/`
- админка: `http://127.0.0.1:8000/admin/`

Если `POSTGRES_DB` не задан, проект использует SQLite. Для разработки это нормально.

## Первый сценарий

1. Войдите в админку.
2. Создайте людей в `Люди`: каждому задается PIN и роль.
3. Создайте места хранения в `Места и держатели`.
4. Откройте `Оборудование` и нажмите `Зарегистрировать оборудование`.
5. Распечатайте QR-наклейки.
6. Сканируйте QR телефоном и выдавайте или возвращайте предметы.

## Важные документы

- [Разработка на macOS, Linux и Windows](docs/DEVELOPMENT.md)
- [Production-развертывание на Ubuntu в Hyper-V](docs/DEPLOYMENT_UBUNTU_HYPERV.md)
- [Бэкапы и восстановление](docs/BACKUP_AND_RESTORE.md)

## QR URL

QR-код хранит абсолютную ссылку. Для работы с телефона в локальной сети задайте адрес сервера в `.env`:

```bash
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10
```

Если запускаете dev-сервер напрямую на порту `8000`, укажите порт:

```bash
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10:8000
```

## Проверки

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test inventory
```
