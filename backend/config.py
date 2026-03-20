from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "./data/missingarr.db"
    log_level: str = "INFO"
    tz: str = "Europe/Berlin"
    version: str = "dev"
    app_name: str = "Missingarr"
    max_log_entries: int = 10000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
