FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.0.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION" \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN poetry install --no-interaction --no-ansi --only main

COPY taurus_supervisor/ ./taurus_supervisor/
COPY taurus_pm/ ./taurus_pm/

RUN useradd -m -u 1000 taurus \
    && chown -R taurus:taurus /app
USER taurus

CMD ["python", "-m", "taurus_supervisor.main"]