FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

LABEL org.opencontainers.image.title="Missingarr" \
      org.opencontainers.image.description="Automated missing content & upgrade searcher for Sonarr and Radarr" \
      org.opencontainers.image.url="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.source="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12-slim" \
      org.opencontainers.image.base.digest="sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"

ARG APP_VERSION=dev

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY backend/ ./backend/
COPY templates/ ./templates/
COPY static/ ./static/
COPY VERSION ./VERSION
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]

ENV DATABASE_URL=/data/missingarr.db \
    LOG_LEVEL=INFO \
    TZ=Europe/Berlin \
    VERSION=${APP_VERSION} \
    PUID=1000 \
    PGID=1000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# Single worker required — agents run as threads within one process.
# The graceful timeout ends open SSE streams so the lifespan shutdown runs (C-L7).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "5"]
