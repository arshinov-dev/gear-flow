FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system gearflow \
    && adduser --system --ingroup gearflow --home /app gearflow \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R gearflow:gearflow /app

COPY requirements.txt /app/
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . /app/
RUN chmod +x /app/docker/entrypoint.sh \
    && chown -R gearflow:gearflow /app

USER gearflow

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "gearflow.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
