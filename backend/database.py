import logging
import os
import sqlite3
from contextlib import contextmanager

from backend.config import settings

logger = logging.getLogger("missingarr.database")

BUSY_TIMEOUT_SECONDS = 30

# app_settings key: local time of the first start of 0.8.0. Season and series
# cache keys written before it do not block episodes (A9).
ANCESTOR_RULE_SINCE_SETTING = "ancestor_rule_since"


def get_connection() -> sqlite3.Connection:
    # A writer holding the lock for a few seconds must not turn a search that
    # *arr already accepted into an untracked one (B2).
    conn = sqlite3.connect(
        settings.database_url, timeout=BUSY_TIMEOUT_SECONDS, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
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


_SCHEMA = """
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
                error_message   TEXT,
                verified_count  INTEGER NOT NULL DEFAULT 0
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
                created_at     TEXT,
                last_checked_at TEXT
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
"""

# (table, column, definition) for every column added after a table was first
# released. Existing databases get the missing ones; fresh ones have them
# from _SCHEMA already.
_COLUMN_MIGRATIONS = [
    ("search_history_items", "item_type", "TEXT NOT NULL DEFAULT 'episode'"),
    ("searched_items", "item_type", "TEXT NOT NULL DEFAULT 'episode'"),
    ("searched_items", "title", "TEXT NOT NULL DEFAULT ''"),
    # Command verification. The 'legacy' default settles existing rows in one
    # go: they carry no command id and can never be verified.
    ("search_history_items", "command_id", "INTEGER"),
    ("search_history_items", "command_status", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("search_history_items", "cache_key", "TEXT NOT NULL DEFAULT ''"),
    ("search_history_items", "verified_at", "TEXT"),
    ("search_history_items", "created_at", "TEXT"),
    ("search_history", "verified_count", "INTEGER NOT NULL DEFAULT 0"),
    # Fair rotation and "expire only after asking" in verify_commands (B4, B5).
    ("search_history_items", "last_checked_at", "TEXT"),
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _COLUMN_MIGRATIONS:
        if column in _columns(conn, table):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            # Only a column that appeared in the meantime is harmless. A bad
            # default, an I/O error or a missing table must stop the start
            # instead of leaving a half-migrated schema behind (B8).
            if "duplicate column name" not in str(exc).lower():
                raise


def _assert_schema(conn: sqlite3.Connection) -> None:
    missing = [
        f"{table}.{column}"
        for table, column, _ in _COLUMN_MIGRATIONS
        if column not in _columns(conn, table)
    ]
    if missing:
        raise RuntimeError("Database schema incomplete after migration: " + ", ".join(missing))


def _restrict_file_permissions() -> None:
    """The database holds the encrypted API keys and, without SECRET_KEY, the
    keys to decrypt them — keep it readable by the app user only (C5). SQLite
    creates -wal/-shm with the permissions of the database file."""
    path = settings.database_url
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    for candidate in (path, f"{path}-wal", f"{path}-shm"):
        try:
            if os.path.exists(candidate):
                os.chmod(candidate, 0o600)
        except OSError as exc:
            logger.warning("Could not restrict permissions of %s: %s", candidate, exc)


def init_db():
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        _add_missing_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_items_pending "
            "ON search_history_items(command_status)"
        )

    # Needs its own connection with foreign keys off — see the docstring.
    _widen_history_status_check()

    with get_db() as conn:
        # After the rebuild: DROP TABLE removes the table's indexes with it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_instance_started "
            "ON search_history(instance_id, started_at DESC, id DESC)"
        )
        # First start of 0.8.0. Season and series keys cached before it were
        # written under the old rules (show_batch, no air-date check) and must
        # not block episodes now (A9, see search_missing._blocked).
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now','localtime'))",
            (ANCESTOR_RULE_SINCE_SETTING,),
        )
        _assert_schema(conn)

    _restrict_file_permissions()


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
