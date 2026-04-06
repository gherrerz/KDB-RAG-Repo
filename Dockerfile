# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --create-home app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

ENV PATH="/opt/venv/bin:${PATH}"

COPY src ./src
COPY .env.example ./.env.example

RUN mkdir -p /app/storage/chroma /app/storage/workspace /app/storage/bm25 \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "-m", "src.main", "--host", "0.0.0.0", "--port", "8000"]
