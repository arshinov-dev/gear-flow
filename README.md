# Gear Flow

Система учета оборудования, комплектов и мерча для медиацентра.

## Главное

Подробная рабочая инструкция: [docs/OPERATIONS.md](docs/OPERATIONS.md)

## Быстрая разработка

```bash
bash scripts/dev.sh
```

Открыть:

```text
http://127.0.0.1:8000/work/
```

## Быстрая установка на Ubuntu

Если код уже лежит на сервере:

```bash
cd /opt/gear-flow
bash scripts/doctor.sh
bash scripts/prod-install.sh
```

Если нужно сразу скачать из Git:

```bash
sudo apt update && sudo apt install -y git && sudo git clone <repo-url> /opt/gear-flow && sudo chown -R "$USER":"$USER" /opt/gear-flow && cd /opt/gear-flow && bash scripts/prod-install.sh
```

## Обновление сервера

```bash
cd /opt/gear-flow
bash scripts/doctor.sh
bash scripts/prod-update.sh
```

## Ручной backup

```bash
cd /opt/gear-flow
bash scripts/backup.sh manual
```

Автоматический backup ставится при `prod-install.sh` и запускается каждый день в `03:00`.

## Восстановление

```bash
cd /opt/gear-flow
RESTORE_ENV=1 bash scripts/restore.sh /path/to/gearflow_backup_YYYYMMDD_HHMMSS_manual.tar.gz
```

## Проверки разработки

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test inventory
```
