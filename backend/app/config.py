from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment variables (prefix-free)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Where source data comes from: local files (dev / docker compose) or S3 (AWS).
    data_source: Literal["file", "s3"] = "file"
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    s3_bucket: str | None = None
    s3_prefix: str = ""
    crm_file: str = "crm_events.json"
    calendar_file: str = "calendar_events.json"

    # Naive timestamps in either source are interpreted in this zone.
    firm_timezone: str = "America/New_York"
    # Attendees outside this domain are treated as external (clients).
    firm_email_domain: str = "firma.com"

    cors_origins: list[str] = ["http://localhost:4200"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
