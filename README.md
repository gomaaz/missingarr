# Missingarr

A lightweight alternative to [Huntarr.io](https://huntarr.io) with one single purpose: automatically search for **missing and upgrade-eligible titles** in your **Sonarr** and **Radarr** instances.

> **Disclaimer:** This is a 100 % vibe-coded project — built for personal use and shared as-is. It does not aim to replicate all Huntarr features, just the core search loop in a minimal footprint.

- Configurable search intervals, rate limiting, quiet hours
- Optional checked search: missingarr checks every release with a pre-filter and grabs only clean ones (dry run first)
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
| **Search again after profile changes** | `On` | A cached title may be searched again once its quality profile changed in \*arr (scores, qualities, custom formats, release profiles). See *Profile changes* below. |
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

### Checked search

Some indexers attach the searched movie's or series' ID to *every* result of an ID search, foreign titles included. Radarr and Sonarr then accept a foreign title as the searched one — "The Thing" (1982) gets `Das.Ding.aus.einer.anderen.Welt.1951…`, "Halloween" (1978) gets `Halloween.2018…`. With **Checked search** missingarr does not send the search command. It fetches the results itself (`GET /api/v3/release`), checks every approved release with a pre-filter and grabs only the first one that passes (`POST /api/v3/release`). Language and quality stay with your \*arr profiles.

| Field | Default | Description |
|---|---|---|
| **Checked search** | `Off` | **Off**: search command as before. **Dry run**: search and check, grab nothing, remember nothing; each title once per round. **Active**: grab the first clean release. |
| **Release search timeout** | `120` s | How long to wait for the release search (10–600). |
| **Time budget per run** | `25` min | No new title is started after this (1–1440). |
| **Dry run: max releases checked** | `100` | Releases checked per title in a dry run (1–1000). |
| **Search again if still missing after (days)** | `7` | A title the checked search grabbed that is still in the Wanted list (missing, or cutoff unmet) after this many days is searched again, independent of *Retry* (1–365). Takes the place of *Redownload Failed from Interactive Search* in \*arr. |

Radarr rules: year within a tolerance of the movie's years (optionally including its release dates), veto when `/parse` names another movie of the library, title match (exact, prefix, word match), releases without a year need an exact title, and the release of the existing file is never grabbed again. Sonarr rules (single episodes only — the pack modes are locked while checked search is on): veto when `/parse` names another series, country/year suffix must fit the series, releases published long before the air date are rejected (shortly before: noted), and the release of the existing file is skipped. Every rule and limit is a setting of the instance; the info icons in the form explain each one.

Review a dry run on the **Pre-filter** page (filter "only differences" shows where the filter takes something else than \*arr; "current round only" hides the verdicts from before the last *Reset dry run*) or download it as CSV. Every title is checked once per round — a Force Run does not change that — and again when its quality profile or the rule settings change (the page marks older rows "profile changed" or "settings changed"). In active mode a title without a clean release counts as searched — *Retry* decides when it is searched again. The History shows these titles as "geladen" (grabbed) or "kein sauberer Treffer" (no clean hit); they need no command verification.

Things to know before you switch it on:

- `GET /api/v3/release` is an **interactive search** for Radarr and Sonarr. It asks the indexers that have *Interactive Search* enabled, not the ones with *Automatic Search* that the search command used. While any indexer has the two switches set differently (or the indexer list cannot be read), a checked run **pauses** — also a run that would find nothing to search: it searches and remembers nothing and ends as a success with the note "Checked search paused" in the History; the activity log warns and names the indexer (or why the list could not be read). *Last sync* on the card stays at the last run that searched, so a lasting pause shows there. Set both switches alike under *Settings → Indexers* (or in Prowlarr).
- The grab names its target: missingarr sends the movie (or the series and episodes) together with the quality and languages the search reported (`shouldOverride`). Radarr and Sonarr keep search results for 30 minutes per indexer and release, mapped to whichever search returned them last; without the target a search for another title in between could redirect the grab. A release the search did not map to exactly this title is never grabbed.
- A failing indexer does not make `GET /api/v3/release` fail: Radarr and Sonarr answer with what the other indexers found and skip an indexer they blocked after failures. So when a title ends without a clean release, missingarr waits a few seconds and reads the \*arr health checks (`GET /api/v3/health`). If they report an indexer with *Interactive Search* on as unavailable due to failures (or cannot be read), the title ends as an error ("indexer failure during search") and is not remembered: the next run searches it again. A permanently broken indexer therefore keeps those titles coming back until it is fixed or switched off.
- A release grabbed through `POST /api/v3/release` is recorded as an interactive grab. Radarr and Sonarr then skip their "matched by ID — Manual Import required" block and import it automatically. Switch off *Settings → Download Clients → Failed Download Handling → Redownload Failed from Interactive Search* in both: missingarr searches a grabbed title again itself once it is still missing after **Search again if still missing after (days)**. A foreign release that gets past the pre-filter (same title and a year within the tolerance — the rules cannot tell such movies apart) is imported, not held back.
- A grab without a clear answer (timeout, connection lost after sending, server error) may be downloading: the title stays blocked like after a grab ("grab uncertain" on the Pre-filter page) — check the queue in \*arr, reset the cache on the Progressed page to retry sooner. A refused grab (for example the search result expired) leaves the title free.
- Season packs: in a search for one episode Sonarr rejects packs itself, except for specials (season 0); the checked search never takes a pack. Nor does it take a multi-episode release (one release for two or more episodes, which Sonarr approves in a search for either of them): the rules and the Searched cache would only cover the episode searched for. An episode that only exists as part of such a release needs the search command (Checked search Off).
- Sonarr upgrades: the checked search searches and remembers single episodes, the search command whole seasons. Back on the search command (Checked search Off), a season waits while one of its episodes is blocked after a checked grab (for *Search again if still missing after (days)*, or until its profile changes) — the season search would search that episode again.
- What \*arr approved during the search is what is grabbed: `POST /api/v3/release` downloads the release without a new decision by \*arr (no second profile check at the grab).
- A release search cannot be interrupted. Deleting an instance while it runs can answer "still stopping" for up to the *Release search timeout* — delete again afterwards.

The API: `GET /api/checked-search` (list, `X-Total-Count`, optional `current_round=true`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`.

### Profile changes

Radarr and Sonarr score every release with the quality profile in force when they search. Missingarr notices when a profile changes: at the start of every run it reads the quality profiles, custom formats and release profiles (no indexer load) and keeps a fingerprint per profile of what decides a score — the qualities and their order, cutoff and scores, the conditions of the custom formats, the terms of the release profiles. Names, labels and help texts do not count, nor does the order of unordered lists; a rule field that a future \*arr version adds counts once missingarr knows it. With **Search again after profile changes** (on by default) a title in the Searched cache may be searched again as soon as the fingerprint of its profile differs from the one it was searched under. A changed custom format or release profile counts as a change of every profile. Titles cached before 0.9.0 count as searched under the profiles as they were at the first run of 0.9.0 — the update alone releases nothing. If the profiles cannot be read, nothing is released. A dry run always checks a title again after its profile changed; the Pre-filter page marks such rows "profile changed". Every detected change is named in the activity log.

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
- Checked search keeps only titles and numbers of a release. `downloadUrl` (which carries your indexer's API key), `magnetUrl`, `infoUrl` and `guid` are never logged or stored; the `guid` (with the release's mapping, quality and languages) is held in memory for the one `POST /api/v3/release`.

## Upgrading to 0.9.0

- Nothing changes until you switch **Checked search** on for an instance; it is off for every existing instance.
- Start with **Dry run** and review the Pre-filter page before switching to **Active**. A dry run searches your indexers like a normal run (one search per title), but over titles that were searched before as well.
- Before the first dry run, set *Automatic Search* and *Interactive Search* alike for every indexer in Radarr and Sonarr — a checked run pauses while they differ (see *Checked search*) — and switch off *Redownload Failed from Interactive Search* in both.
- With Checked search on, Sonarr only searches single episodes; Season Packs, Show Batch and Smart cannot be selected.
- **Search again after profile changes** is on for every instance. The first run of 0.9.0 records the profiles; titles already in the Searched cache stay blocked until a profile changes after that. Each run reads the quality profiles, custom formats and release profiles, and Sonarr also the series list (no indexer load).
- The pre-filter log keeps the history's retention (`HISTORY_RETENTION_DAYS`).

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
