from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for Telegram Business Watcher Bot."""

    bot_token: str
    db_path: str = "business_watcher.db"
    retention_days: int = 14
    port: int | None = None
    render_external_url: str | None = None
    ping_interval_seconds: int = 300
    allowed_user_ids: str | list[int] = [965180470, 226583258]
    last_business_connection_id: str | None = None
    redis_url: str | None = None

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_user_ids(cls, v: Any) -> list[int]:
        if isinstance(v, str):
            clean = v.strip()
            if not clean:
                return []
            if clean.startswith("[") and clean.endswith("]"):
                import json

                try:
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        return [int(x) for x in parsed]
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            return [
                int(x.strip())
                for x in clean.split(",")
                if x.strip().lstrip("-").isdigit()
            ]
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    def is_user_allowed(self, user_id: int | None) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id is not None and user_id in self.allowed_user_ids

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings(env_file_path: str | Path | None = None) -> Settings:
    if env_file_path and Path(env_file_path).exists():
        return Settings(_env_file=str(env_file_path))  # type: ignore
    return Settings()
