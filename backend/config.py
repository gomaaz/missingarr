import secrets
from pathlib import Path
from pydantic_settings import BaseSettings

# Read version from VERSION file as fallback when env var is not set
def _read_version_file() -> str:
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

    # Auth — leave AUTH_PASSWORD empty to disable authentication (dev/trusted-network only)
    auth_username: str = "admin"
    auth_password: str = ""
    secret_key: str = secrets.token_hex(32)  # Override via SECRET_KEY env var

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
