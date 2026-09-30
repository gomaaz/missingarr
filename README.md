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
| `TZ` | `Europe/Berlin` | Timezone for quiet hours, display and stored timestamps |
| `PUID` / `PGID` | `1000` | User and group the app runs as. On start the container hands `/data` to them (only files that do not belong to them yet; symlinks are left alone), removes all group/other permissions there, then drops root. Pick an ID that no login user on the host has — whoever owns `/data` can read the API keys. `PUID=0` keeps root. |
| `AUTH_USERNAME` | `admin` | Login name |
| `AUTH_PASSWORD` | — | Plain text or a bcrypt hash (`$2b$…`). Create a hash with `python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"`; in `docker-compose.yml` write every `$` as `$$`. Without it a temporary password is printed in the log. |
| `SECRET_KEY` | — | Optional. When set, the key that encrypts the \*arr API keys and the session key are derived from it (HKDF-SHA256). On the first start with it, stored API keys are re-encrypted and the old keys are deleted from the database. From then on the same `SECRET_KEY` is required — without it, or with a different one, missingarr refuses to start. Keep a copy. |
| `COOKIE_SECURE` | `false` | Mark the login cookies `Secure`. Only when missingarr is reached over HTTPS. |
| `HISTORY_RETENTION_DAYS` | `365` | Finished search runs older than this are deleted once per hour. `0` keeps them forever. Open runs are never deleted. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | uvicorn setting. Set it to your reverse proxy's IP so login throttling sees the real client address and the cross-site check sees `https` from `X-Forwarded-Proto`. |

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
| **Retry (hours)** | `0` | How long a searched title stays in the Searched cache. `0` = never search it again automatically (reset the cache on the Progressed page to retry). A broader season or series search only blocks episodes that were already out (air date plus *Hours after release*) when it ran; season and series searches from before 0.8.0 do not block single episodes. |
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

## Security notes

- API keys are never sent back to the browser. The API shows `********` and `api_key_set`. Leave the key field empty to keep the stored key; when you change the URL you must enter the key again.
- Without `SECRET_KEY` the database holds the encrypted API keys *and* the key to decrypt them — treat backups of `/data` as secret. The database file is created with mode `600`, and the container makes everything in `/data` private to `PUID` on start. Copies such as `missingarr.db.bak-…` keep the keys they were made with, also after a switch to `SECRET_KEY` — delete them or store them as carefully.
- Lost `SECRET_KEY`: the stored API keys cannot be decrypted any more. Stop the container, run `DELETE FROM app_settings WHERE key IN ('key_source','secret_key_check'); UPDATE instances SET api_key='';` on `data/missingarr.db` with any SQLite tool, start again (with a new `SECRET_KEY` or none) and enter every API key again in the instance form.
- "Remember me" lasts 30 days on the server side. Signing out ends every session and every remembered login on all devices. Changing `AUTH_PASSWORD` does the same.
- After 5 failed sign-ins from one address, further attempts are blocked for 30 seconds, doubling up to 15 minutes. Every failed attempt is logged with the address.
- State-changing requests from other sites are rejected (checked with `Sec-Fetch-Site`/`Origin`). Scripts without these headers, such as `curl`, keep working.
- Missingarr does not follow redirects from Sonarr/Radarr. If the connection test reports a redirect, fix the URL.

## Upgrading to 0.8.0

- Everyone has to sign in once again (the remember-me cookie format changed).
- On the first start the container hands `./data` to `PUID:PGID` (default `1000:1000`) and makes it private to that user. If UID 1000 is a login user on your host, set another `PUID`/`PGID` first.
- On the first start, finished search runs older than `HISTORY_RETENTION_DAYS` (default 365) are deleted.
- Sign out is now a button that sends a POST; `GET /logout` no longer works.
- If `SECRET_KEY` is already set in your compose file, 0.8.0 re-encrypts the stored API keys with it on the first start. Back up `./data` before upgrading and keep the value.

## Compatibility

| App | Version |
|---|---|
| Sonarr | v4.x (API v3) |
| Radarr | v6.x (API v3) |

Authentication via `X-Api-Key` header only (Radarr v6 removed Basic Auth).

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
uvicorn backend.main:app --reload
```

Front-end libraries are vendored in `static/vendor`. To update them, change version and npm integrity in `scripts/vendor_assets.py` and run it. Runtime dependencies are locked with hashes in `requirements.lock` (pip-compile --generate-hashes).

Nothing updates by itself any more. To renew the pins:

- Python packages: `pip-compile --upgrade --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --output-file=requirements.lock requirements.txt` (from `pip-tools`), then run the tests.
- Base image: `docker buildx imagetools inspect python:3.12-slim` shows the current index digest; put it in the `FROM` line and in the `org.opencontainers.image.base.digest` label of the `Dockerfile`.
- GitHub Actions: `git ls-remote https://github.com/<owner>/<action> "refs/tags/<tag>^{}" "refs/tags/<tag>"` shows the commit of a tag; replace the SHA in `.github/workflows/docker-publish.yml` and keep the tag as a comment.

## Disclaimer

> **This is a 100% vibe coding project.**
> Built entirely with AI assistance (Claude Code). No guarantees — use at your own risk.

## License

MIT
