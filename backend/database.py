import logging
import sqlite3
from contextlib import contextmanager
from backend.config import settings

logger = logging.getLogger("missingarr.database")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_url, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS instances (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                name                     TEXT NOT NULL,
                type                     TEXT NOT NULL CHECK(type IN ('sonarr','radarr')),
                url                      TEXT NOT NULL,
                api_key                  TEXT NOT NULL,

                enabled                  INTEGER NOT NULL DEFAULT 1,
                search_missing_enabled   INTEGER NOT NULL DEFAULT 1,
                search_upgrades_enabled  INTEGER NOT NULL DEFAULT 0,

                interval_minutes         INTEGER NOT NULL DEFAULT 15,
                retry_hours              INTEGER NOT NULL DEFAULT 0,

                rate_window_minutes      INTEGER NOT NULL DEFAULT 60,
                rate_cap                 INTEGER NOT NULL DEFAULT 25,

                search_order             TEXT NOT NULL DEFAULT 'random'
                                         CHECK(search_order IN ('random','smart','newest_first','oldest_first')),
                missing_mode             TEXT NOT NULL DEFAULT 'episode'
                                         CHECK(missing_mode IN ('smart','season_packs','show_batch','episode')),
                missing_per_run          INTEGER NOT NULL DEFAULT 5,
                upgrades_per_run         INTEGER NOT NULL DEFAULT 1,
                seconds_between_actions  INTEGER NOT NULL DEFAULT 2,
                hours_after_release      INTEGER NOT NULL DEFAULT 9,

                upgrade_source           TEXT NOT NULL DEFAULT 'monitored_items_only'
                                         CHECK(upgrade_source IN ('wanted_list_only','monitored_items_only','both')),

                quiet_start              TEXT,
                quiet_end                TEXT,

                connection_status        TEXT NOT NULL DEFAULT 'unknown'
                                         CHECK(connection_status IN ('unknown','online','offline','error')),
                last_seen_at             TEXT,

                created_at               TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at               TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id     INTEGER REFERENCES instances(id) ON DELETE CASCADE,
                instance_name   TEXT NOT NULL,
                skill           TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
                wanted_count    INTEGER NOT NULL DEFAULT 0,
                triggered_count INTEGER NOT NULL DEFAULT 0,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                status          TEXT NOT NULL DEFAULT 'running'
                                CHECK(status IN ('running','success','error',
                                                 'pending','partial','failed','unverified')),
                error_message   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_history_instance ON search_history(instance_id);
            CREATE INDEX IF NOT EXISTS idx_history_started  ON search_history(started_at DESC);

            CREATE TABLE IF NOT EXISTS activity_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id   INTEGER REFERENCES instances(id) ON DELETE CASCADE,
                instance_name TEXT NOT NULL,
                level         TEXT NOT NULL CHECK(level IN ('info','warn','error','debug')),
                skill         TEXT,
                message       TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);

            CREATE TABLE IF NOT EXISTS search_history_items (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id         INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
                title          TEXT NOT NULL,
                arr_id         INTEGER,
                item_type      TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series')),
                command_id     INTEGER,
                command_status TEXT NOT NULL DEFAULT 'legacy',
                cache_key      TEXT NOT NULL DEFAULT '',
                verified_at    TEXT,
                created_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_history_items_run ON search_history_items(run_id);

            CREATE TABLE IF NOT EXISTS searched_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                cache_key   TEXT NOT NULL,
                title       TEXT NOT NULL,
                item_type   TEXT NOT NULL,
                searched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(instance_id, cache_key)
            );

            CREATE INDEX IF NOT EXISTS idx_searched_instance ON searched_items(instance_id);
            CREATE INDEX IF NOT EXISTS idx_searched_at ON searched_items(searched_at DESC);

            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Reset any previously auto-migrated retry_hours back to 0 (permanent cache)
        conn.execute("UPDATE instances SET retry_hours=0 WHERE retry_hours IN (1, 168)")

        # Migrations: add columns introduced after initial release
        for sql in [
            "ALTER TABLE search_history_items ADD COLUMN item_type TEXT NOT NULL DEFAULT 'episode'",
            "ALTER TABLE searched_items ADD COLUMN item_type TEXT NOT NULL DEFAULT 'episode'",
            "ALTER TABLE searched_items ADD COLUMN title TEXT NOT NULL DEFAULT ''",
            # Command verification. The 'legacy' default settles existing rows in
            # one go: they carry no command id and can never be verified, so they
            # must not enter the verification loop.
            "ALTER TABLE search_history_items ADD COLUMN command_id INTEGER",
            "ALTER TABLE search_history_items ADD COLUMN command_status TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE search_history_items ADD COLUMN cache_key TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE search_history_items ADD COLUMN verified_at TEXT",
            "ALTER TABLE search_history_items ADD COLUMN created_at TEXT",
            "ALTER TABLE search_history ADD COLUMN verified_count INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_items_pending "
            "ON search_history_items(command_status)"
        )

    # Needs its own connection with foreign keys off — see the docstring.
    _widen_history_status_check()


def _widen_history_status_check() -> None:
    """Allow the verification statuses on search_history.status.

    SQLite cannot alter a CHECK constraint, so the table has to be rebuilt.
    Foreign keys must be off while that happens: search_history_items
    references search_history with ON DELETE CASCADE, and with foreign_keys=ON
    the DROP TABLE would cascade and take every history item with it.

    PRAGMA foreign_keys is a no-op inside a transaction, hence the dedicated
    connection rather than the shared get_db() context manager.
    """
    conn = sqlite3.connect(settings.database_url, check_same_thread=False)
    try:
        current = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_history'"
        ).fetchone()
        if not current or "'pending'" in current[0]:
            return  # fresh database, or already widened

        before = conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=OFF")
        # isolation_level=None hands transaction control to us. The rebuild must
        # be one atomic step: sqlite3.executescript() COMMITS any open
        # transaction before it runs, so using it here would drop and rename the
        # table outside the transaction — a crash between the two would leave no
        # search_history at all.
        conn.isolation_level = None
        conn.execute("BEGIN")
        for statement in [
            """
            CREATE TABLE search_history_rebuilt (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id     INTEGER REFERENCES instances(id) ON DELETE CASCADE,
                instance_name   TEXT NOT NULL,
                skill           TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
                wanted_count    INTEGER NOT NULL DEFAULT 0,
                triggered_count INTEGER NOT NULL DEFAULT 0,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                status          TEXT NOT NULL DEFAULT 'running'
                                CHECK(status IN ('running','success','error',
                                                 'pending','partial','failed','unverified')),
                error_message   TEXT,
                verified_count  INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            INSERT INTO search_history_rebuilt
                (id, instance_id, instance_name, skill, wanted_count, triggered_count,
                 started_at, finished_at, status, error_message, verified_count)
            SELECT id, instance_id, instance_name, skill, wanted_count, triggered_count,
                   started_at, finished_at, status, error_message, COALESCE(verified_count, 0)
            FROM search_history
            """,
            "DROP TABLE search_history",
            "ALTER TABLE search_history_rebuilt RENAME TO search_history",
            "CREATE INDEX IF NOT EXISTS idx_history_instance ON search_history(instance_id)",
            "CREATE INDEX IF NOT EXISTS idx_history_started  ON search_history(started_at DESC)",
        ]:
            conn.execute(statement)

        moved = conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
        if moved != before:
            conn.execute("ROLLBACK")
            raise RuntimeError(f"history rebuild would lose rows: {before} before, {moved} after")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            conn.execute("ROLLBACK")
            raise RuntimeError(f"history rebuild left {len(violations)} broken references")

        conn.execute("COMMIT")
        logger.info("Widened search_history.status (%d runs preserved)", moved)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # nothing open to roll back
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


_cached_secret_key: str | None = None


def get_or_create_secret_key() -> str:
    """Return a stable secret key persisted in the DB.

    Using this instead of the env-generated Settings.secret_key means
    the session cookie stays valid across Docker restarts.
    """
    global _cached_secret_key
    if _cached_secret_key:
        return _cached_secret_key
    import secrets as _s
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='secret_key'"
        ).fetchone()
        if row:
            _cached_secret_key = row[0]
        else:
            _cached_secret_key = _s.token_hex(32)
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('secret_key', ?)",
                (_cached_secret_key,),
            )
    return _cached_secret_key
