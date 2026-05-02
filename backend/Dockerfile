# syntax=docker/dockerfile:1.7

# ---------- Stage 1: builder (instala dependencias en wheels) ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencias de sistema necesarias para compilar psycopg2-binary, lxml, fpdf, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

# ---------- Stage 2: runtime (imagen final mínima) ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    LOG_LEVEL=INFO

# Solo libs runtime: libpq5 (cliente postgres), libxml2, libxslt, libjpeg, zlib.
# postgresql-client se incluye para que el contenedor pueda hacer pg_dump/pg_restore
# durante la migración inicial si se necesita.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libxml2 \
        libxslt1.1 \
        libjpeg62-turbo \
        zlib1g \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Usuario no-root (mejor postura de seguridad)
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Instala dependencias desde los wheels precompilados del stage anterior
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

# Copia el código de la app y ajusta permisos
COPY --chown=app:app . /app

USER app

EXPOSE 8000

# Healthcheck simple para que Render sepa si el contenedor está vivo
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --max-time 4 "http://127.0.0.1:${PORT}/" || exit 1

# uvicorn directo (sin gunicorn) — Render Starter es 0.5 CPU, un solo worker es óptimo.
# --proxy-headers para que respete los X-Forwarded-* del load balancer de Render.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
