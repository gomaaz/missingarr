FROM python:3.12-slim

LABEL org.opencontainers.image.title="Missingarr" \
      org.opencontainers.image.description="Automated missing content & upgrade searcher for Sonarr and Radarr" \
      org.opencontainers.image.url="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.source="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY templates/ ./templates/
COPY static/ ./static/

VOLUME ["/data"]

ENV DATABASE_URL=/data/missingarr.db \
    LOG_LEVEL=INFO \
    TZ=Europe/Berlin

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Single worker required — agents run as threads within one process
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
