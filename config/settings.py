from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for Telegram Business Watcher Bot."""

    bot_token: str
    db_path: str = "business_watcher.db"
    retention_days: int = 14
    port: int | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings(env_file_path: str | Path | None = None) -> Settings:
    if env_file_path and Path(env_file_path).exists():
        return Settings(_env_file=str(env_file_path))  # type: ignore
    return Settings()
