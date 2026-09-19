FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

# Non-root user
RUN useradd --create-home discovery && chown -R discovery:discovery /app
USER discovery

EXPOSE 8000

# Default command runs the API; docker-compose overrides this for the worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
