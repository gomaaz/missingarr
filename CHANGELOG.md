# Changelog

All notable changes to missingarr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (0.x: any release may change behaviour). Docker images are published per version as `gomaaz/missingarr:<version>`; release notes are on [GitHub Releases](https://github.com/gomaaz/missingarr/releases).

## [Unreleased]

## [0.9.0] - 2026-10-02

### Added

- **Checked search** (pre-filter), per instance: *Off*, *Dry run* or *Active*. Some indexers attach the searched movie's or series' ID to every result of an ID search, foreign titles included, and Radarr and Sonarr then accept a foreign release as the searched title ("The Thing" (1982) gets `Das.Ding.aus.einer.anderen.Welt.1951…`, "Halloween" (1978) gets `Halloween.2018…`). With checked search on, missingarr does not send the search command. It fetches the results itself (`GET /api/v3/release`), checks every approved release with a pre-filter that catches such ID-stamped foreign titles and grabs only the first one that passes (`POST /api/v3/release`). Quality and language stay with your \*arr profiles.
  - Radarr rules: year within a tolerance of the movie's years (optionally counting its cinema, digital and physical release dates), veto when `/parse` names another movie of the library, title match (exact, prefix, word match), releases without a year need an exact title, the release of the existing file is never grabbed again.
  - Sonarr rules (single episodes only): veto when `/parse` names another series, a country or year suffix must fit the series, releases published long before the air date are rejected (shortly before: noted), the release of the existing file is skipped. Season packs and multi-episode releases are never grabbed.
  - Every rule and limit is a setting of the instance, each with an info icon: *Release search timeout* (120 s), *Time budget per run* (25 min), *Dry run: max releases checked* (100), *Search again if still missing after (days)* (7) and the rule settings of the app.
- **Dry run**: searches and checks like an active run, but grabs nothing and remembers nothing. It also checks titles that were searched before, each title once per round (a Force Run does not change that), and again after its quality profile or the rule settings changed. *Reset dry run* starts a new round, and so does switching an instance to *Dry run*.
- **Pre-filter page**: counters per instance and outcome; filters for instance, mode, outcome and text; "only differences" (the filter takes something else than \*arr would) and "current round only"; expandable candidates with verdict, reasons and notes; "profile changed" and "settings changed" badges; *Reset dry run* per instance; CSV download of the current filter.
- Instance cards show a "dry run" or "checked" badge. The History labels checked titles as "geladen" (grabbed) or "kein sauberer Treffer" (no clean hit); they need no command verification, so such a run is finished at once.
- **Profile fingerprints** (every instance, checked search or not): at the start of every run missingarr reads the quality profiles, custom formats, release profiles, quality size limits (`GET /api/v3/qualitydefinition`) and indexer settings (`GET /api/v3/config/indexer`), all without indexer load, and keeps a fingerprint per profile of what decides a score or the approval of a release. Names, labels, help texts and the order of unordered lists do not count. Every detected change is named in the activity log.
- New instance setting **Search again after profile changes** (on by default): a title in the Searched cache may be searched again as soon as the fingerprint of its profile differs from the one it was searched under.
- Safeguards of the checked search:
  - **Indexer pause**: `GET /api/v3/release` is an interactive search and asks the indexers with *Interactive Search* on, not the ones with *Automatic Search*. While any indexer has the two switches set differently, or the indexer list cannot be read, a checked run pauses: no search, nothing remembered, status "success" with the note "Checked search paused" and a warning in the activity log that names the indexer.
  - **Health detection**: a failing indexer does not make the release search fail. When a title ends without a clean release, missingarr waits a few seconds and reads the \*arr health checks (`GET /api/v3/health`). If they report an indexer the search asks (*Interactive Search* on and, for a tagged indexer, a tag shared with the movie or series) as unavailable, or cannot be read, the title ends as an error and is not remembered.
  - **Error pause**: a title whose check ends in an error is not remembered as searched, but sits out 6 hours, then 12, then 24 for every further error (an error more than a day after the last pause ended starts at 6 again), in dry runs and Force Runs too. It no longer takes a place of *Missing per run* or *Upgrades per run* on every run while the error lasts. The pause is stored in the database and survives a restart; any other result and a reset of the cache on the Progressed page (*Reset*, *Reset All*) end it.
  - **Queue check**: an active run reads the \*arr queue once before it picks its titles and leaves out titles that are already downloading, before *per run* is counted (not searched, not remembered). Right before each release search and again right before the grab it reads the title's queue and the title itself; if it is in the queue now, has a file now or another file than before, nothing is grabbed ("changed meanwhile") and the next run decides again. Releases held back by a delay profile and failed downloads do not count.
  - **Grab cooldown**: a title grabbed from a Wanted list (missing, or cutoff unmet) stays blocked for *Search again if still missing after (days)*, also when *Retry* is shorter (a changed profile releases it earlier, like any cached title). If it is still listed after that, it is searched again. This takes the place of *Redownload Failed from Interactive Search* in \*arr. Radarr upgrades from *Monitored Items Only* keep *Retry*: the movie list does not tell whether a grab is still missing.
  - **Empty-search release**: a title whose search returned no approved release at all ("no results") is searched again after *Search again if still missing after (days)*, also with *Retry* 0 (a shorter *Retry* frees it earlier). An indexer failure can drop out of the health checks before missingarr reads them, so an empty list is no proof. "No clean hit" (results came, the filter rejected all) stays with *Retry*.
  - **Fixed grab target**: the grab names the movie (or the series and episode) together with the quality and languages the search reported (`shouldOverride`), so a search for another title in between cannot redirect it. A release the search did not map to exactly this title is never grabbed.
  - **Unclear grabs**: a grab without a clear answer (timeout, connection lost after sending, server error) blocks the title like a grab ("grab uncertain"); a refused grab leaves it free after its error pause. After a failed grab no second release is tried.
  - Switching an instance off, deleting it or shutting down also stops a checked run after the release search, before every `/parse` call and right before the grab. Every release search counts as one action against the rate limit. After *Time budget per run* no new title is started.
- API: `GET /api/checked-search` (list with `X-Total-Count`, optional `current_round=true`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`. Instance responses also carry `checked_search`, `checked_search_settings`, `dry_run_round`, `dry_run_round_started_at`, `search_again_after_profile_change`, `profile_fingerprints` and `profile_fingerprints_baseline`; a `PUT` without `checked_search`, `checked_search_settings` or `search_again_after_profile_change` keeps the stored value.

### Changed

- Sonarr with checked search on searches single episodes only: *Season Packs*, *Show Batch* and *Smart* are locked in the form and rejected by the API with `422`, also for a `PUT` without `checked_search` when the stored instance already uses it.
- Sonarr upgrades in checked mode are searched and remembered per episode instead of per season. The cache key of the other search path blocks as well, so switching the mode releases nothing early. Back on the search command, a season waits while one of its episodes is blocked after a checked grab.
- A run whose items all have a verdict already is closed at once instead of after the next verification pass (up to two minutes later). Besides the checked search this affects runs where \*arr returned no command id.
- Housekeeping also deletes pre-filter log rows older than `HISTORY_RETENTION_DAYS`, except the dry-run rows of the current round, and error pauses that no longer count. Cache rows of a checked grab are kept for *Search again if still missing after (days)*, also outside *Retry*.

### Fixed

- A command that \*arr reports as failed (or orphaned after a restart) releases the Searched cache entry only if the entry still belongs to that command (new column `searched_items.history_item_id`). Before, a late failure of an old command could remove the block of a newer search of the same title, also after the history had been cleared or trimmed.

### Security

- The checked search keeps only titles and numbers of a release. `downloadUrl` (which carries your indexer's API key), `magnetUrl`, `infoUrl` and `guid` are never logged or stored; the `guid` with the release's mapping, quality and languages is held in memory for the one `POST /api/v3/release`.
- The CSV export neutralises cells that start with `=`, `+`, `-`, `@`, a tab or a carriage return, so a release name cannot start a spreadsheet formula. It reads one database snapshot, so rows written or reset during a download are neither doubled nor lost.

### Upgrade notes

- Back up `./data` before upgrading. The new columns (`instances.checked_search`, `checked_search_settings`, `dry_run_round`, `dry_run_round_started_at`, `search_again_after_profile_change`, `profile_fingerprints`, `profile_fingerprints_baseline`; `searched_items.profile_fingerprint`, `grabbed_at`, `no_results_at`, `history_item_id`) and the tables `checked_search_log` and `checked_search_pauses` are added automatically on the first start.
- Nothing changes in how missingarr searches until you switch **Checked search** on: it is *Off* for every existing instance.
- Start with **Dry run** and review the Pre-filter page (especially "only differences") before switching to **Active**. A dry run searches your indexers like a normal run (one search per title), but over titles that were searched before as well.
- Before the first dry run, set *Automatic Search* and *Interactive Search* alike for every indexer in Radarr and Sonarr (*Settings → Indexers*, or in Prowlarr). A checked run pauses while they differ.
- Before switching to **Active**, switch off *Redownload Failed from Interactive Search* (*Settings → Download Clients → Failed Download Handling*) in Radarr and Sonarr. A release grabbed through `POST /api/v3/release` counts as an interactive grab: \*arr skips its "matched by ID — Manual Import required" block and imports it automatically, and missingarr searches a grabbed title that is still missing again by itself. A foreign release that gets past the pre-filter (same title and a year within the tolerance) is imported, not held back.
- The fixed grab target (`shouldOverride`) was checked against the source code of Radarr 6.4 and Sonarr 4.0. An older version may ignore it; compare your versions before switching to **Active**.
- With *Retry* 0, titles the search command already searched stay in the Searched cache and an active checked search leaves them out. Clear the cache on the Progressed page if they should be checked again.
- Delay profiles:
  - As with the search command, \*arr treats missingarr's searches as user-invoked and does not wait for the delay of a delay profile: a clean release is grabbed at once.
  - A release \*arr holds back in its queue because of a delay profile does not count as a download. The title is still searched, and \*arr drops the held-back release after missingarr's grab.
  - The Usenet and Torrent switches of a delay profile still decide which releases \*arr approves, but a change to a delay profile (or to the settings of a single indexer, such as minimum seeders or required flags) does not count as a profile change and releases nothing. Clear the cache on the Progressed page if titles should be searched again after such a change.
- With checked search on, Sonarr only searches single episodes: set *Missing Mode* to *Episode* first. An episode that only exists as part of a multi-episode release needs the search command (Checked search *Off*).
- **Search again after profile changes** is on for every instance. The first run of 0.9.0 records the profiles; titles cached before stay blocked until a profile changes after that, the update alone releases nothing. A later change to a custom format, release profile, quality size limit or indexer setting counts as a change of every profile and releases every cached title (each run still searches only *per run* titles, within the rate cap); switch the setting off where that is not wanted. Each run reads the quality profiles, custom formats, release profiles, quality size limits and indexer settings, and Sonarr also the series list (no indexer load).
- The pre-filter log keeps the history's retention (`HISTORY_RETENTION_DAYS`).
- A release search cannot be interrupted: deleting an instance while one runs can answer "still stopping" for up to the *Release search timeout*. Delete again afterwards.

## [0.8.0] - 2026-09-30

### Added

- `SECRET_KEY` (optional): when set, the key that encrypts the \*arr API keys and the session key are derived from it (HKDF-SHA256) instead of being stored in the database. On the first start with it, stored API keys are re-encrypted and the old keys deleted; from then on the same value is required.
- `COOKIE_SECURE` marks the login cookies `Secure` (only when missingarr is reached over HTTPS).
- `HISTORY_RETENTION_DAYS` (default `365`, `0` keeps everything) with hourly housekeeping: finished search runs older than that are deleted, and so are expired Searched cache rows when *Retry* is above 0. It runs for disabled instances too; open runs and runs whose commands still await a verdict are kept.
- `PUID` / `PGID` (default `1000`): the container starts as root only to hand `/data` to that user, makes everything in it private and then drops root. `PUID=0` keeps root.
- Login throttling: after 5 failed sign-ins from one address, further attempts are blocked for 30 seconds, doubling up to 15 minutes (`429`). Every failed attempt is logged with the address. `FORWARDED_ALLOW_IPS` is documented for reverse proxies.
- Failed submissions are recorded: the History shows such titles as failed (without a command id), and a run names how many failed and the first error.
- The instance card shows `ERROR` when the scheduler cannot start and labels the next search run.

### Changed

- Sign out is a button that sends a POST; `GET /logout` answers `405`. Signing out ends every session and every remembered login on all devices, and so does changing `AUTH_PASSWORD`.
- "Remember me" uses a new token format (v2) that expires after 30 days on the server side and can be revoked.
- `POST /api/instances/<id>/trigger` answers `409` while that skill is already running (also with `force=false`; before, the call was accepted with `200` and silently dropped). A forced run no longer waits up to 90 seconds.
- `/api/*` without a session answers `401 {"detail": "Not authenticated"}` instead of redirecting to the login page. htmx calls also get `HX-Redirect`, so the browser lands on the login page and returns afterwards.
- List limits are validated: a `limit` above the maximum or below 1 answers `422` (`/api/history` ≤ 200, `/api/history/items` ≤ 1000, `/api/searched` ≤ 500, `/api/activity` ≤ 500). `DELETE /api/history` answers with `deleted` and `kept_open` and keeps open runs.
- *Newest First*, *Oldest First* and *Smart* order work over the whole Wanted list: it is read in pages of 1000 to the end (more requests per run for big lists; an emergency brake ends the run as an error above 1,000,000 entries). *Random* reads at most 10 pages per run.
- Sonarr cache rule: every missing mode checks the episode, season and series keys. A season or series search blocks an episode only if it ran after the episode's air date plus *Hours after release* and after the first start of 0.8.0, so season and series searches from before 0.8.0 do not block single episodes.
- Sonarr season and series density counts only monitored, aired episodes (the series without season 0), so running seasons get single-episode searches more often.
- Radarr release date: a movie counts as released from the earlier of its digital and physical release, otherwise from its cinema release. *Hours After Release* counts from that date, and *Newest First* and *Oldest First* sort by it.
- Upgrades reach every page of the cutoff list. If every upgrade source fails, the run is an error instead of "no candidates".
- Honest run status: a run in which every submission fails is an error; a run with some failures names them, and its verification is at most "partial".
- Command verification also runs during quiet hours (no searches). `orphaned` counts as failed and releases the cache entry. After 24 hours an item expires only once \*arr itself answered; a proxy error (502/503/504), 401/403 and redirects are no answer.
- Disabling or deleting an instance aborts a running search at once; saving the form does not.
- The rate window survives saving an instance or switching it off and on (a container restart still resets it), and rate slots are reserved atomically.
- Skill switches on the card take effect at once, without a restart.
- Stored intervals outside 1–10080 minutes are clamped to the limit with a warning in the log.
- The instance form enforces the bounds of every field. Saving fails for `*_per_run` 0 while that search is on, names over 100 characters or with control characters, and URLs with `?`, `#` or a user name and password.
- The Logs page shows the last 100 entries on open, the History filters on the server (including "Upgrade"), the Progressed page pages on the server and names the instance, and FORCE is locked while a run is active.
- The connection test reports a wrong API key or a redirect instead of HTTP 0.

### Fixed

- A command \*arr accepted that could not be stored in the database is kept in memory and blocks its title; the run stops as an error that names the command id, and the next run stores it.
- Two triggers arriving at once could both answer "started", and one of the runs was dropped.
- Runs left "running" by a restart are closed on start.
- Submissions, their history items and cache entries are stored in one transaction.
- `retry_hours` is no longer reset by a migration; columns are migrated explicitly.
- The instance form sent a second request, a GET with every field (the API key included) in the query string.
- The live log stream stayed open while a page sat in the back/forward cache, so after a few navigations the next page hung. Its waiters are cancelled when the browser leaves, and streams end on shutdown, so an open tab no longer delays `docker stop`.
- Every card started a second countdown timer.
- Pages no longer ask for a favicon that does not exist.
- `AUTH_PASSWORD` as a bcrypt hash works (as usual with bcrypt, only the first 72 bytes of the password count).
- An instance whose search does not stop in time is kept instead of deleted (`409` "still stopping").

### Security

- API keys are never sent back to the browser: the API shows `********` and `api_key_set`, and pages do not contain them. Saving with an empty or masked key keeps the stored one; changing the URL requires the key again (`400`).
- State-changing requests from other sites are rejected: POST, PUT and DELETE with a foreign `Origin` (scheme included) or a `Sec-Fetch-Site` other than `same-origin`/`none` answer `403`. Scripts without these headers, such as `curl`, keep working.
- After login, `next` only accepts local paths.
- Requests to Sonarr and Radarr never follow redirects, so the API key stays with the configured host.
- Instance names are kept out of inline JavaScript.
- `bcrypt` is used directly; `passlib` was removed.
- The container runs without root, the database file is created with mode `600` and the data directory is made private to `PUID` on start.
- Alpine.js and htmx are vendored in `static/vendor` and verified against their npm integrity hashes; no CDN is used.
- Runtime dependencies are locked with hashes (`requirements.lock`, installed with `--require-hashes`), the base image is pinned by digest and the GitHub Actions by commit SHA.

### Upgrade notes

- Everyone has to sign in once again (the remember-me cookie format changed). This also applies to scripts that keep an old session cookie.
- On the first start the container hands `./data` to `PUID:PGID` (default `1000:1000`) and makes it private to that user, backup copies in it included. If UID 1000 is a login user on your host, set another `PUID`/`PGID` first.
- On the first start, finished search runs older than `HISTORY_RETENTION_DAYS` (default 365) are deleted.
- Sign out is now a button that sends a POST; `GET /logout` and bookmarks to it no longer work.
- `POST /api/instances/<id>/trigger` answers `409` while that skill runs; `curl -f` then exits with code 22.
- If `SECRET_KEY` is already set in your compose file, 0.8.0 re-encrypts the stored API keys with it on the first start. Back up `./data` before upgrading and keep the value. Copies such as `missingarr.db.bak-…` keep the keys they were made with.
- Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to its address so login throttling sees the real client address.
- If Sonarr or Radarr (or a proxy in front of them) answers with a redirect, the instance shows as offline with "redirect"; fix the URL.

## [0.7.0] - 2026-08-16

### Added

- Command verification: missingarr stores the command id \*arr returns for every search and asks \*arr every two minutes what became of it (`GET /api/v3/command/{id}`, new skill `verify_commands`).
- The History shows the verified outcome per title in a new column, with the command id in the badge tooltip. The run status follows the verification: a run that sent commands stays "pending" until \*arr reported their outcome, then ends as success, partial, failed or unverified.
- The instance card shows "Confirmed / Sent" instead of "Triggered (last)"; the left number counts commands \*arr confirmed.

### Changed

- A command \*arr reports as failed, aborted or cancelled releases its Searched cache entry, so the title is searched again even with *Retry* 0. An outcome \*arr no longer knows (404) counts as unverified, not as a failure, and releases nothing.
- Each skill has its own lock instead of one per instance, so the hourly health check no longer logs "Already running" during a search run.
- History entries from before 0.7.0 are marked as legacy and show a dash instead of an outcome.

### Fixed

- Season and series searches stored the id of the episode that triggered them instead of the series that was searched; upgrade searches did the same.
- The rebuild of the `search_history` table during the migration is atomic; before, a crash in the middle could lose the table.
- History, activity log and the Searched list sort by id when entries share a second, so the newest really comes first; log trimming removes the oldest rows within a second.

## [0.6.13] - 2026-03-26

### Fixed

- The Docker image installs `tzdata`, so `TZ` applies to all timestamps (they were in UTC before).

## [0.6.12] - 2026-03-26

### Fixed

- Sonarr: the missing list is no longer sorted by air date on the API side, which left out specials, season 0 and episodes without an air date.

## [0.6.11] - 2026-03-26

### Fixed

- Radarr: the missing list is no longer sorted by physical release on the API side, which left out movies without a physical release date (such as streaming-only titles).

## [0.6.10] - 2026-03-25

### Fixed

- Removed the 10-page cap, so every missing title is reachable through page cycling.

## [0.6.9] - 2026-03-25

### Changed

- *Retry (hours)* defaults to `0` again (never search automatically again); instances with 1 or 168 are migrated to 0.

### Fixed

- Page cycling tries every page until *Missing per run* is met.

## [0.6.8] - 2026-03-25

### Changed

- *Retry (hours)* defaults to `168` (one week); instances with 0 or 1 are migrated.

### Fixed

- When the first page yields too few candidates, further random pages are tried, so runs no longer find nothing once a page is fully cached.

## [0.6.7] - 2026-03-25

### Fixed

- Timestamps in the database and the UI use local time (UTC only for scheduling and \*arr dates).

## [0.6.6] - 2026-03-25

### Changed

- `docker-compose.yml` mounts `/etc/localtime` read-only.

## [0.6.5] - 2026-03-25

### Changed

- *Retry (hours)* defaults to `0` (never expire); instances with 1 are migrated, and the card shows "off".

### Fixed

- Searching a cached title again refreshes its timestamp, so titles no longer come back on every run.

## [0.6.4] - 2026-03-23

### Removed

- Input limits in the instance form and validators.

## [0.6.3] - 2026-03-23

### Fixed

- A past season search also blocks the single episodes of that season. Season Packs, Show Batch and Smart store the key of the command actually sent, and Smart no longer sends two season searches for one season in a run.

## [0.6.2] - 2026-03-23

### Fixed

- Season Packs and Show Batch check how many episodes already exist and fall back to a narrower search instead of searching existing episodes again.

## [0.6.1] - 2026-03-22

### Changed

- Saving an instance stays on the edit page.

## [0.6.0] - 2026-03-22

### Fixed

- Broad bug fix across skills, agents and UI: Smart mode caches per episode, Smart order no longer leaves slots empty, page rotation reaches the last page, the card values are updated after every run (upgrades included), Radarr upgrade candidates no longer overlap with the missing search, the live log does not reconnect while paused, a stopped instance shows no stale countdown, and a connection test timeout answers `504`.

## [0.5.6] - 2026-03-22

### Fixed

- The Edit link on the cards works with htmx boost.

## [0.5.5] - 2026-03-22

### Changed

- Tab navigation without full page reloads (htmx boost).

## [0.5.4] - 2026-03-22

### Fixed

- Random rotation includes page 1, and upgrades rotate over all pages of the cutoff list.

## [0.5.3] - 2026-03-22

### Fixed

- *Retry (hours)* is applied to the Searched cache; cached titles were skipped for good before.

## [0.5.2] - 2026-03-22

### Fixed

- Every page failed with newer Starlette versions.

## [0.5.1] - 2026-03-22

### Changed

- Switching an instance on or off updates only its card instead of reloading the page.

## [0.5.0] - 2026-03-22

### Fixed

- *Random* order reaches the whole backlog; upgrades fetch a larger pool and shuffle it, so different titles come up.

## [0.4.9] - 2026-03-22

### Changed

- The Logs page renders bursts of live entries at once.

## [0.4.8] - 2026-03-22

### Fixed

- The missing search skips records that already have a file.

## [0.4.7] - 2026-03-22

### Changed

- No upper limit for *Missing per run*.

## [0.4.6] - 2026-03-22

### Changed

- Status polling of running instances no longer queries the database.

## [0.4.5] - 2026-03-22

### Fixed

- The skill switches in the instance form are saved.

## [0.4.4] - 2026-03-22

### Fixed

- Upgrade candidates are deduplicated within a run; Sonarr upgrades are cached per season.

## [0.4.3] - 2026-03-22

### Fixed

- No duplicate season or series searches within one run.

## [0.4.2] - 2026-03-22

### Fixed

- The skill switches in the instance form show the saved values.

## [0.4.1] - 2026-03-22

### Fixed

- The Missing and Upgrades buttons on the cards show their state.

## [0.4.0] - 2026-03-22

### Added

- Missing and Upgrades switches on the dashboard cards.
- Upgrade search for Sonarr (cutoff-unmet list).

## [0.3.11] - 2026-03-22

### Changed

- Sonarr looks up only the series whose titles the Wanted list leaves out, instead of loading the whole series list.

## [0.3.10] - 2026-03-22

### Fixed

- Force runs ignore *Hours after release*.

## [0.3.9] - 2026-03-22

### Fixed

- Dashboard cards update every 5 seconds without a page reload.

## [0.3.8] - 2026-03-22

### Fixed

- Sonarr force searches failed on episodes without a season number and on timestamps without a time zone.

## [0.3.7] - 2026-03-22

### Fixed

- Force runs ignore the Searched cache.

## [0.3.6] - 2026-03-22

### Fixed

- Force triggers work even when the scheduled search of that skill is off.

## [0.3.5] - 2026-03-22

### Fixed

- A force trigger waits for an active run instead of being dropped; an expired session leads to the login page instead of a false "Run triggered".

## [0.3.4] - 2026-03-22

### Added

- "Remember me for 30 days" on the login page; the session key is kept in the database, so sessions survive a restart.

### Fixed

- Empty series titles in the History and on the Progressed page.

## [0.3.3] - 2026-03-21

### Changed

- The "Searched" page is now called "Progressed".

### Fixed

- Existing databases get the `item_type` column (searches ran, but nothing was stored); Sonarr records without a series are handled.

## [0.3.2] - 2026-03-21

### Changed

- The History loads its entries after the page is shown.

## [0.3.1] - 2026-03-21

### Fixed

- The History broke when a title contained a double quote.

## [0.3.0] - 2026-03-21

### Changed

- The History lists every searched title in one table with filters for instance, type and text and with pagination.

## [0.2.9] - 2026-03-21

### Fixed

- Middleware order, so the login check sees the session.

## [0.2.8] - 2026-03-21

### Changed

- Authentication is always on. Without `AUTH_PASSWORD` a temporary password is printed in the log.

## [0.2.7] - 2026-03-21

### Fixed

- Search settings changed in the UI apply to the next run without a restart.

## [0.2.6] - 2026-03-21

### Fixed

- The searched titles of a run are shown as a table.

## [0.2.5] - 2026-03-21

### Fixed

- The version shown in the app always matches the Docker image (`VERSION` file).

## [0.2.4] - 2026-03-21

### Fixed

- Dark-mode styling of select fields.

## [0.2.3] - 2026-03-21

### Security

- \*arr API keys are encrypted at rest (Fernet); the key is created on the first start.

## [0.2.2] - 2026-03-21

### Fixed

- A force trigger ignores quiet hours, and two runs of the same skill no longer run at once.

## [0.2.1] - 2026-03-21

### Added

- Session login with `AUTH_USERNAME` and `AUTH_PASSWORD` (plain text or bcrypt hash), optional at first.

## [0.2.0] - 2026-03-21

### Fixed

- *Random* order picks from a larger pool, and cached titles are filtered out before the titles of a run are picked.

## [0.1.9] - 2026-03-21

### Added

- Searched cache: titles that were searched are skipped; new page with counts per instance and a reset per instance or for all.

## [0.1.8] - 2026-03-21

### Changed

- The Logs page is a paginated table with filters for instance and level.

## [0.1.7] - 2026-03-21

### Changed

- The card header holds the connection test and an ON/OFF switch.

## [0.1.6] - 2026-03-21

### Changed

- The README explains every instance setting and shows an example setup.

## [0.1.5] - 2026-03-21

### Fixed

- The Docker image carries the version of its git tag.

## [0.1.4] - 2026-03-21

### Fixed

- The History page failed to render the searched titles of a run.

## [0.1.3] - 2026-03-21

### Fixed

- Syntax error in the upgrade search.

## [0.1.2] - 2026-03-21

### Added

- The History lists every searched title of a run.

### Changed

- All UI texts are in English.

## [0.1.0] - 2026-03-20

### Added

- Initial release: searches for missing titles and upgrades in Sonarr and Radarr instances, several instances side by side.
- Search order *Random*, *Smart*, *Newest First*, *Oldest First*; Sonarr missing mode *Episode*, *Season Packs*, *Show Batch*, *Smart*.
- Rate limiting with a rolling window, quiet hours, hours after release.
- Web UI with dashboard and countdown, search history, live log and help.
- Single Docker container with SQLite, multi-arch image (amd64, arm64) on Docker Hub.

[Unreleased]: https://github.com/gomaaz/missingarr/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/gomaaz/missingarr/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/gomaaz/missingarr/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/gomaaz/missingarr/compare/v0.6.13...v0.7.0
[0.6.13]: https://github.com/gomaaz/missingarr/compare/v0.6.12...v0.6.13
[0.6.12]: https://github.com/gomaaz/missingarr/compare/v0.6.11...v0.6.12
[0.6.11]: https://github.com/gomaaz/missingarr/compare/v0.6.10...v0.6.11
[0.6.10]: https://github.com/gomaaz/missingarr/compare/v0.6.9...v0.6.10
[0.6.9]: https://github.com/gomaaz/missingarr/compare/v0.6.8...v0.6.9
[0.6.8]: https://github.com/gomaaz/missingarr/compare/v0.6.7...v0.6.8
[0.6.7]: https://github.com/gomaaz/missingarr/compare/v0.6.6...v0.6.7
[0.6.6]: https://github.com/gomaaz/missingarr/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/gomaaz/missingarr/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/gomaaz/missingarr/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/gomaaz/missingarr/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/gomaaz/missingarr/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/gomaaz/missingarr/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/gomaaz/missingarr/compare/v0.5.6...v0.6.0
[0.5.6]: https://github.com/gomaaz/missingarr/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/gomaaz/missingarr/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/gomaaz/missingarr/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/gomaaz/missingarr/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/gomaaz/missingarr/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/gomaaz/missingarr/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/gomaaz/missingarr/compare/v0.4.9...v0.5.0
[0.4.9]: https://github.com/gomaaz/missingarr/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/gomaaz/missingarr/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/gomaaz/missingarr/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/gomaaz/missingarr/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/gomaaz/missingarr/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/gomaaz/missingarr/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/gomaaz/missingarr/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/gomaaz/missingarr/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/gomaaz/missingarr/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/gomaaz/missingarr/compare/v0.3.11...v0.4.0
[0.3.11]: https://github.com/gomaaz/missingarr/compare/v0.3.10...v0.3.11
[0.3.10]: https://github.com/gomaaz/missingarr/compare/v0.3.9...v0.3.10
[0.3.9]: https://github.com/gomaaz/missingarr/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/gomaaz/missingarr/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/gomaaz/missingarr/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/gomaaz/missingarr/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/gomaaz/missingarr/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/gomaaz/missingarr/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/gomaaz/missingarr/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/gomaaz/missingarr/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/gomaaz/missingarr/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/gomaaz/missingarr/compare/v0.2.9...v0.3.0
[0.2.9]: https://github.com/gomaaz/missingarr/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/gomaaz/missingarr/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/gomaaz/missingarr/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/gomaaz/missingarr/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/gomaaz/missingarr/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/gomaaz/missingarr/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/gomaaz/missingarr/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/gomaaz/missingarr/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/gomaaz/missingarr/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/gomaaz/missingarr/compare/v0.1.9...v0.2.0
[0.1.9]: https://github.com/gomaaz/missingarr/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/gomaaz/missingarr/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/gomaaz/missingarr/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/gomaaz/missingarr/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/gomaaz/missingarr/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/gomaaz/missingarr/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/gomaaz/missingarr/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/gomaaz/missingarr/compare/v0.1.0...v0.1.2
[0.1.0]: https://github.com/gomaaz/missingarr/releases/tag/v0.1.0
