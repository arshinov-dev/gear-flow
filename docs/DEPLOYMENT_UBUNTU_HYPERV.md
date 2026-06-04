# Production на Ubuntu в Hyper-V

Цель: один сервер в студии, доступный по локальному IP, с PostgreSQL, Gunicorn, Nginx, systemd и регулярными бэкапами.

## Минимальные требования

Минимум для маленькой студии:

- 2 vCPU.
- 4 GB RAM.
- 60 GB диска.
- Ubuntu Server LTS.
- Внешний Hyper-V virtual switch, чтобы телефоны и компьютеры студии видели VM в локальной сети.

Комфортнее:

- 4 vCPU.
- 8 GB RAM.
- 100 GB+ диска.
- Отдельный диск или сетевое хранилище для бэкапов.
- UPS для Windows-хоста.

Для Windows-хоста важно отключить сон, настроить автоматический старт VM и хранить бэкапы вне виртуального диска: на отдельном диске, NAS или Windows-share.

## Схема

```text
телефон/ноутбук -> http://192.168.1.10 -> Nginx :80 -> Gunicorn 127.0.0.1:8001 -> Django -> PostgreSQL
```

## 1. Подготовить сеть Hyper-V

1. В Hyper-V Manager создайте `External` virtual switch.
2. Подключите VM к этому switch.
3. Закрепите IP за VM. Лучший вариант - DHCP reservation на роутере. Альтернатива - статический IP в Ubuntu через Netplan.

Пример Netplan, имя интерфейса проверьте командой `ip addr`:

```yaml
network:
  version: 2
  ethernets:
    eth0:
      addresses:
        - 192.168.1.10/24
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses:
          - 192.168.1.1
          - 1.1.1.1
```

Применение:

```bash
sudo netplan try
sudo netplan apply
```

## 2. Установить системные пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx postgresql postgresql-client
```

Проверьте службы:

```bash
systemctl status postgresql
systemctl status nginx
```

## 3. Создать пользователя приложения

```bash
sudo adduser --system --group --home /opt/gear-flow gearflow
sudo usermod -aG www-data gearflow
sudo mkdir -p /opt/gear-flow
sudo chown gearflow:www-data /opt/gear-flow
```

## 4. Развернуть код

Если код лежит в Git:

```bash
sudo -u gearflow git clone <repo-url> /opt/gear-flow
```

Если копируете архивом, распакуйте его в `/opt/gear-flow` и выставьте владельца:

```bash
sudo chown -R gearflow:www-data /opt/gear-flow
```

## 5. Настроить PostgreSQL

Создайте роль и базу:

```bash
sudo -u postgres psql
```

Внутри `psql`:

```sql
CREATE ROLE gearflow LOGIN PASSWORD 'replace-with-long-password';
CREATE DATABASE gearflow OWNER gearflow TEMPLATE template0 ENCODING 'UTF8';
\q
```

Проверьте вход:

```bash
PGPASSWORD='replace-with-long-password' psql -h localhost -U gearflow -d gearflow -c 'select 1;'
```

## 6. Создать `.env`

```bash
sudo -u gearflow cp /opt/gear-flow/.env.example /opt/gear-flow/.env
sudo -u gearflow nano /opt/gear-flow/.env
```

Минимальный production-вариант для локальной сети без HTTPS:

```bash
DJANGO_SECRET_KEY=replace-with-long-random-secret
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=192.168.1.10,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://192.168.1.10
GEARFLOW_PUBLIC_BASE_URL=http://192.168.1.10

POSTGRES_DB=gearflow
POSTGRES_USER=gearflow
POSTGRES_PASSWORD=replace-with-long-password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

GEARFLOW_BACKUP_DIR=/mnt/gearflow-backups
GEARFLOW_BACKUP_KEEP=7
```

Если позже добавите HTTPS, включите:

```bash
DJANGO_CSRF_TRUSTED_ORIGINS=https://gearflow.example.local
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_SECURE_PROXY_SSL_HEADER=1
GEARFLOW_PUBLIC_BASE_URL=https://gearflow.example.local
```

## 7. Установить Python-зависимости

```bash
sudo -u gearflow python3 -m venv /opt/gear-flow/.venv
sudo -u gearflow /opt/gear-flow/.venv/bin/python -m pip install --upgrade pip
sudo -u gearflow /opt/gear-flow/.venv/bin/pip install -r /opt/gear-flow/requirements.txt
```

## 8. Применить Django-команды

```bash
cd /opt/gear-flow
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py migrate'
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py collectstatic --noinput'
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py createsuperuser'
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py check --deploy'
```

Для локального HTTP `check --deploy` будет ругаться на HTTPS-настройки. Это ожидаемо, если сервер не выходит в интернет и работает только внутри студии. Для публичного доступа предупреждения нужно закрывать, а не игнорировать.

## 9. Подключить systemd

```bash
sudo cp /opt/gear-flow/scripts/gearflow.service.example /etc/systemd/system/gearflow.service
sudo systemctl daemon-reload
sudo systemctl enable --now gearflow
sudo systemctl status gearflow
```

Логи:

```bash
journalctl -u gearflow -f
```

## 10. Подключить Nginx

```bash
sudo cp /opt/gear-flow/scripts/gearflow.nginx.example /etc/nginx/sites-available/gearflow
sudo nano /etc/nginx/sites-available/gearflow
sudo ln -s /etc/nginx/sites-available/gearflow /etc/nginx/sites-enabled/gearflow
sudo nginx -t
sudo systemctl reload nginx
```

Если открывается стандартная страница Nginx, отключите default site:

```bash
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 11. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw enable
sudo ufw status
```

## 12. Первый smoke test

Откройте:

- `http://192.168.1.10/work/`
- `http://192.168.1.10/admin/`

Проверьте:

- вход в админку;
- вход в `/work/` пользователем с ролью ответственного;
- создание тестового места хранения;
- регистрацию тестового оборудования;
- открытие QR-ссылки с телефона.

## Обновление проекта без потери базы

Обычный порядок:

```bash
cd /opt/gear-flow
sudo -u gearflow bash scripts/gearflow-backup-full.example.sh
sudo systemctl stop gearflow
sudo -u gearflow git pull
sudo -u gearflow .venv/bin/pip install -r requirements.txt
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py migrate'
sudo -u gearflow bash -c 'set -a; source .env; set +a; .venv/bin/python manage.py collectstatic --noinput'
sudo systemctl start gearflow
sudo systemctl status gearflow
```

Миграции Django рассчитаны на сохранение существующих данных. Риск появляется, если обновление специально удаляет поле, модель или меняет смысл данных. Поэтому перед обновлением всегда делайте backup и смотрите список миграций.

## Источники

- Django deployment checklist: https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/
- Django WSGI deployment: https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
- Django static files deployment: https://docs.djangoproject.com/en/dev/howto/static-files/deployment/
- Gunicorn deployment: https://gunicorn.org/deploy/
- Nginx reverse proxy: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy
- Ubuntu networking / Netplan: https://ubuntu.com/server/docs/explanation/networking/configuring-networks/
- Hyper-V VM creation: https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/create-a-virtual-machine-in-hyper-v
