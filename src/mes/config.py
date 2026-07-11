"""Application settings, loaded from environment variables and .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MES runtime configuration.

    All variables are read with the ``MES_`` prefix, e.g. ``MES_DATABASE_URL``.
    """

    model_config = SettingsConfigDict(
        env_prefix="MES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="development")
    database_url: str
    log_level: str = Field(default="INFO")


def get_settings() -> Settings:
    """Load settings, raising a clear error if required variables are missing."""
    try:
        return Settings()
    except Exception as exc:
        raise RuntimeError(
            "Failed to load MES settings. Ensure required environment variables are set "
            "(e.g. MES_DATABASE_URL), either in the environment or in a .env file. "
            "See .env.example for the full list."
        ) from exc
