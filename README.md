# Missingarr

A lightweight alternative to [Huntarr.io](https://huntarr.io) with one single purpose: automatically search for **missing and upgrade-eligible titles** in your **Sonarr** and **Radarr** instances.

> **Disclaimer:** This is a 100 % vibe-coded project — built for personal use and shared as-is. It does not aim to replicate all Huntarr features, just the core search loop in a minimal footprint.

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

## Container Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `/data/missingarr.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| `TZ` | `Europe/Berlin` | Timezone for quiet hours and display |

## Instance Settings

Each Sonarr or Radarr instance has its own configuration. Here is a full explanation of every field.

### Basic

| Field | Description |
|---|---|
| **Name** | A label for this instance, shown in the dashboard and logs. E.g. `Radarr 4K` or `Sonarr Main`. |
| **Type** | `Sonarr` for TV shows, `Radarr` for movies. |
| **URL** | Full URL including port. No trailing slash. E.g. `http://192.168.1.10:8989`. |
| **API Key** | Found in your \*arr instance under **Settings → General → API Key**. Leave blank when editing to keep the existing key. |
| **Enabled** | When disabled, the instance is paused and will not run any automatic searches. |
| **Search Missing** | Triggers searches for episodes (Sonarr) or movies (Radarr) that are monitored but have no file yet. |
| **Search Upgrades** | *(Radarr only)* Triggers searches for movies that already have a file but could be upgraded to a better quality. |

### Scheduling

| Field | Default | Description |
|---|---|---|
| **Interval (min)** | `15` | How often Missingarr checks for missing content. Lower = more frequent, but more API calls. |
| **Retry (hours)** | `1` | If a search run finds nothing or fails, how long to wait before trying again. |
| **Quiet Start / End** | — | Time window (HH:MM) during which no automatic searches run. Useful to avoid activity at night. Force runs from the dashboard always bypass quiet hours. |
| **Hours After Release** | `9` | Missingarr waits this many hours after the release date before searching for a title. Prevents hammering indexers for content that isn't out yet. Set to `0` to search immediately. |
| **Seconds Between Actions** | `2` | Delay between individual API calls within a single run. Prevents flooding your indexer. |

### Rate Limiting

| Field | Default | Description |
|---|---|---|
| **Rate Window (min)** | `60` | Rolling time window for rate limiting. Missingarr counts how many searches were triggered within this window. |
| **Rate Cap** | `25` | Maximum number of searches allowed within the rate window. Once the cap is reached, the current run stops early and waits for the window to roll over. |

> **Example:** With Rate Window = 60 and Rate Cap = 25, Missingarr will trigger at most 25 searches per hour, regardless of how often the interval fires.

### Search Behaviour

| Field | Default | Description |
|---|---|---|
| **Missing Per Run** | `5` | How many missing titles are processed in a single run. |
| **Upgrades Per Run** | `1` | How many upgrade candidates are processed in a single run. |
| **Search Order** | `Random` | Order in which missing titles are picked: **Random** (even spread), **Smart** (50% newest / 30% random / 20% oldest), **Newest First**, **Oldest First**. |
| **Missing Mode** | `Episode` | *(Sonarr only)* How missing episodes are searched: **Episode** (one at a time), **Season Packs** (whole season), **Show Batch** (full series), **Smart** (auto: season pack if ≥50% of a season is missing, otherwise single episode). |
| **Upgrade Source** | `Monitored Items Only` | *(Radarr only)* Which movies are considered upgrade candidates: **Wanted List Only** (Radarr's cutoff-unmet list), **Monitored Items Only** (all monitored movies that already have a file), **Both**. |

## Example: Typical Home Setup

Two instances — one Sonarr, one Radarr — running on the same server:

```
Sonarr Main
  Type:                  Sonarr
  URL:                   http://192.168.1.10:8989
  Interval:              30 min
  Retry:                 2 h
  Rate Window:           60 min
  Rate Cap:              20
  Search Order:          Smart
  Missing Mode:          Smart
  Missing Per Run:       5
  Hours After Release:   1
  Seconds Between:       2
  Quiet Hours:           01:00 – 06:00

Radarr Main
  Type:                  Radarr
  URL:                   http://192.168.1.10:7878
  Interval:              30 min
  Retry:                 2 h
  Rate Window:           60 min
  Rate Cap:              20
  Search Missing:        ✓
  Search Upgrades:       ✓
  Missing Per Run:       5
  Upgrades Per Run:      1
  Upgrade Source:        Monitored Items Only
  Hours After Release:   9
  Seconds Between:       2
  Quiet Hours:           01:00 – 06:00
```

With this setup Missingarr will:
- Check every 30 minutes, but never run between 01:00 and 06:00
- Trigger at most 20 searches per hour per instance
- For Sonarr: automatically decide between single-episode and season-pack searches
- For Radarr: also look for quality upgrades on movies that already have a file

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

## Disclaimer

> **This is a 100% vibe coding project.**
> Built entirely with AI assistance (Claude Code). No guarantees — use at your own risk.

## License

MIT
