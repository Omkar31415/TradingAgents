"""Assistant settings, loaded from the repo-root .env via pydantic-settings.

Field names map case-insensitively to environment variables, so
``telegram_bot_token`` reads ``TELEGRAM_BOT_TOKEN``. LLM provider keys
(ANTHROPIC_API_KEY etc.) are read by the tradingagents package itself and are
intentionally not duplicated here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASSISTANT_HOME = Path.home() / ".tradingagents"


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---
    # Default: SQLite under ~/.tradingagents, next to the engine's own state.
    assistant_db_url: str = ""

    # --- LLM used for scheduled runs ---
    # Any provider the engine supports works here: "anthropic" (default),
    # "ollama" for free local models (e.g. deep=qwen3:32b, quick=llama3.2),
    # "openai", "openai_compatible", etc. For Ollama the default endpoint is
    # http://localhost:11434/v1; point elsewhere via ASSISTANT_LLM_BACKEND_URL
    # or the engine's own OLLAMA_BASE_URL.
    assistant_llm_provider: str = "anthropic"
    assistant_deep_model: str = "claude-sonnet-4-6"
    assistant_quick_model: str = "claude-haiku-4-5"
    assistant_llm_backend_url: str = ""  # empty = provider's default endpoint

    # --- Paper portfolio ---
    # Virtual starting cash (USD) for the automatic paper broker. Changing it
    # later does not reset an existing account.
    paper_starting_cash: float = Field(default=10_000.0, gt=0)

    # --- Watchlist rotation ---
    # After this many consecutive Hold ratings a ticker drops from daily to
    # weekly runs; any non-Hold rating promotes it straight back to daily.
    assistant_demote_after_holds: int = Field(default=5, ge=1)

    # --- Run schedule ---
    # Analysis windows are DB-backed "schedule slots" managed from the web
    # dashboard (up to N per day, each with its own time/market/budget).
    # This global cap is the safety net: no matter how slots are configured,
    # at most this many ticker-runs happen per UTC day — protects a limited
    # LLM quota (e.g. Ollama cloud free tier) from a misconfigured schedule.
    assistant_daily_run_budget: int = Field(default=4, ge=1)

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Email (digest per market run) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    @property
    def database_url(self) -> str:
        if self.assistant_db_url:
            return self.assistant_db_url
        db_path = _ASSISTANT_HOME / "assistant.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_username and self.smtp_password and self.email_to)


@lru_cache
def get_settings() -> AssistantSettings:
    return AssistantSettings()
