FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.0.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /app

RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION" \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY taurus_supervisor/ ./taurus_supervisor/
COPY taurus_pm/ ./taurus_pm/
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

RUN useradd -m -u 1000 taurus \
    && mkdir -p /var/log/taurus-supervisor /opt/taurus \
    && chown -R taurus:taurus /app /var/log/taurus-supervisor /opt/taurus

ENTRYPOINT ["/app/docker-entrypoint.sh"]