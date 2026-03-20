# Missingarr

Automated missing content & quality upgrade searcher for **Sonarr** and **Radarr**.

- Configurable search intervals, rate limiting, quiet hours
- Multiple instances (mix of Sonarr + Radarr)
- Live log streaming, search history, dashboard with countdown timers
- Single Docker container, SQLite — no external dependencies

## Quick Start

```yaml
# docker-compose.yml
services:
  missingarr:
    image: gomaaz/missingarr:latest
    container_name: missingarr
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped
```

```bash
docker-compose up -d
```

Open **http://localhost:8000** and add your first instance.

## Configuration

| Env Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `/data/missingarr.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| `TZ` | `Europe/Berlin` | Timezone for quiet hours and display |

## Compatibility

| App | Version |
|---|---|
| Sonarr | v4.x (API v3) |
| Radarr | v6.x (API v3) |

Authentication via `X-Api-Key` header only (Radarr v6 removed Basic Auth).

## Development

```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

## License

MIT
