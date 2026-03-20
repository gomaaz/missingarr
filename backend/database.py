import sqlite3
from contextlib import contextmanager
from backend.config import settings


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
                retry_hours              INTEGER NOT NULL DEFAULT 1,

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

                created_at               TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
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
                                CHECK(status IN ('running','success','error')),
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
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);
        """)
