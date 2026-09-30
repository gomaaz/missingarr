from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _read_version_file() -> str:
    """Fallback when the VERSION env var is not set."""
    for candidate in (Path("VERSION"), Path(__file__).parent.parent / "VERSION"):
        if candidate.exists():
            return candidate.read_text().strip()
    return "dev"


class Settings(BaseSettings):
    database_url: str = "./data/missingarr.db"
    log_level: str = "INFO"
    tz: str = "Europe/Berlin"
    version: str = _read_version_file()
    app_name: str = "Missingarr"
    max_log_entries: int = 10000

    # Auth is always on. Without AUTH_PASSWORD a temporary password is
    # generated at start and printed to the log.
    auth_username: str = "admin"
    auth_password: str = ""

    # Opt-in: when set, the key that encrypts the *arr API keys and the
    # session signing key are derived from it instead of being stored in the
    # database next to the data they protect (C5). Once used it must never
    # change or go missing.
    secret_key: str = ""

    # Mark session and remember-me cookies Secure. Only when missingarr is
    # reached over HTTPS, otherwise the browser drops them and login fails (C6).
    cookie_secure: bool = False

    # Finished search runs older than this many days are deleted; 0 keeps
    # them forever (B7).
    history_retention_days: int = Field(default=365, ge=0)

    # "ignore": a .env copied from .env.example also carries container-only
    # keys such as PUID/PGID; pydantic-settings would reject them otherwise.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
