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

    # Telegram alarm push (optional). If either is empty, alarm delivery is a no-op
    # (logged) — the check still runs and records alerts to the DB.
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # LLM(Phase 3)。留空 = 無法呼叫該 provider(會明確報錯,不靜默假裝成功)。
    # 走與其他機密相同的管道:.env(已 gitignore),絕不硬編、絕不進版控。
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")  # 尚未實作 provider,先留位


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
