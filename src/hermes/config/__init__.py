"""Central settings. Secrets via env only, never committed."""
from __future__ import annotations

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:
    from pydantic import BaseModel, ConfigDict

    BaseSettings = BaseModel  # type: ignore[misc,assignment]
    SettingsConfigDict = ConfigDict  # type: ignore[misc,assignment]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    llm_provider: str = "cloudflare"
    llm_model: str = "@cf/meta/llama-3.1-8b-instruct"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_model: str = "@cf/meta/llama-3.1-8b-instruct"
    cloudflare_timeout: int = 60
    telegram_bot_token: str = ""
    telegram_allowed_users: str = ""
    # ---- Telegram 1:1 chat (webhook + tunnel, local-first) ----
    telegram_webhook_secret: str = ""
    telegram_webhook_url: str = ""
    telegram_session_db: str = "./telegram_sessions.db"
    telegram_mode: str = "webhook"  # webhook | polling
    telegram_max_history: int = 20
    hermes_db_path: str = "./hermes_tasks.db"
    hermes_database_url: str = ""  # set → Postgres backend (psycopg3), else SQLite
    hermes_routing_path: str = "./routing.json"
    hermes_sandbox_dir: str = "./sandbox"
    max_retries: int = 3

    @property
    def allowed_users(self) -> list[str]:
        return [u.strip() for u in self.telegram_allowed_users.split(",") if u.strip()]


settings = Settings()
