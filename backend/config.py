import secrets
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "./data/missingarr.db"
    log_level: str = "INFO"
    tz: str = "Europe/Berlin"
    version: str = "dev"
    app_name: str = "Missingarr"
    max_log_entries: int = 10000

    # Auth — leave AUTH_PASSWORD empty to disable authentication (dev/trusted-network only)
    auth_username: str = "admin"
    auth_password: str = ""
    secret_key: str = secrets.token_hex(32)  # Override via SECRET_KEY env var

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
