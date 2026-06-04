# Бэкапы и восстановление

Бэкап Gear Flow состоит из трех частей:

1. PostgreSQL dump - люди, оборудование, события, комплекты, мерч, остатки.
2. `media/` - загруженные фотографии проблем и другие пользовательские файлы.
3. `.env` - секреты и настройки подключения.

Если сохранить только базу, часть истории с фото будет потеряна. Если потерять `.env`, восстановление тоже усложнится.

## Где хранить

Хороший минимум:

- локально в VM: только временно;
- копия вне VM: отдельный диск, NAS или Windows-share;
- периодическая ручная копия на внешний носитель.

Не храните единственную копию бэкапа внутри того же виртуального диска Hyper-V.

## Настройка каталога

Пример:

```bash
sudo mkdir -p /mnt/gearflow-backups
sudo chown gearflow:gearflow /mnt/gearflow-backups
```

В `/opt/gear-flow/.env`:

```bash
GEARFLOW_BACKUP_DIR=/mnt/gearflow-backups
GEARFLOW_BACKUP_KEEP=7
```

## Ручной бэкап

Полный бэкап:

```bash
cd /opt/gear-flow
sudo -u gearflow bash scripts/gearflow-backup-full.example.sh
```

Только PostgreSQL dump:

```bash
cd /opt/gear-flow
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py backup_postgres'
```

Файлы будут выглядеть примерно так:

```text
gearflow_20260604_030000.dump
gearflow_media_20260604_030000.tar.gz
```

## Ежедневный cron

От имени пользователя `gearflow`:

```bash
sudo -u gearflow crontab /opt/gear-flow/scripts/gearflow-backup-cron.example
sudo -u gearflow crontab -l
```

Проверка логов:

```bash
sudo tail -f /var/log/gearflow-backup.log
```

Если cron не может писать в `/var/log/gearflow-backup.log`, создайте файл заранее:

```bash
sudo touch /var/log/gearflow-backup.log
sudo chown gearflow:gearflow /var/log/gearflow-backup.log
```

## Проверка бэкапа

Раз в месяц сделайте тест восстановления в отдельную базу:

```bash
sudo -u postgres createdb gearflow_restore_test -T template0
pg_restore -h localhost -U gearflow -d gearflow_restore_test /mnt/gearflow-backups/gearflow_YYYYMMDD_HHMMSS.dump
```

После проверки:

```bash
sudo -u postgres dropdb gearflow_restore_test
```

## Восстановление на новом сервере

1. Установите Ubuntu, PostgreSQL, Nginx и зависимости по production-инструкции.
2. Скопируйте код проекта в `/opt/gear-flow`.
3. Скопируйте `.env` из бэкапа или восстановите его вручную.
4. Создайте пустую базу:

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE gearflow LOGIN PASSWORD 'same-or-new-password';
CREATE DATABASE gearflow OWNER gearflow TEMPLATE template0 ENCODING 'UTF8';
\q
```

5. Восстановите PostgreSQL:

```bash
PGPASSWORD='password' pg_restore -h localhost -U gearflow -d gearflow /mnt/gearflow-backups/gearflow_YYYYMMDD_HHMMSS.dump
```

6. Восстановите `media/` и `.env`:

```bash
cd /opt/gear-flow
sudo -u gearflow tar --extract --gzip --file /mnt/gearflow-backups/gearflow_media_YYYYMMDD_HHMMSS.tar.gz
sudo chown -R gearflow:www-data /opt/gear-flow/media /opt/gear-flow/.env
```

7. Примените миграции на всякий случай:

```bash
cd /opt/gear-flow
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py migrate'
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart gearflow
```

8. Проверьте `/admin/`, `/work/`, QR-страницу любого предмета и наличие фотографий в истории.

## Перед обновлением проекта

Всегда:

```bash
cd /opt/gear-flow
sudo -u gearflow bash scripts/gearflow-backup-full.example.sh
```

Потом обновляйте код и применяйте миграции. Так уже созданные люди, оборудование, комплекты и мерч не потеряются даже при неудачном обновлении.

## Источники

- PostgreSQL `pg_dump`: https://www.postgresql.org/docs/17/app-pgdump.html
- PostgreSQL `pg_restore`: https://www.postgresql.org/docs/17/app-pgrestore.html
