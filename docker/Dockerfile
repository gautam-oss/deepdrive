FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/production.txt requirements/production.txt
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir --prefer-binary -r requirements/production.txt

COPY . .

# collectstatic requires SECRET_KEY — pass a dummy at build time only.
# Real secrets are injected at runtime via environment/Secrets Manager.
RUN SECRET_KEY=build-time-placeholder \
    DB_NAME=placeholder \
    DB_USER=placeholder \
    DB_PASSWORD=placeholder \
    FIELD_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Entrypoint: gunicorn with 4 sync workers.
# Override CMD in docker-compose for celery_worker / celery_beat services.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
