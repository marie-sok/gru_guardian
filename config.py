from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRU_GUARDIAN_", case_sensitive=False)

    # Telegram transport. GRU_BOT_TG_KEY is intentionally read explicitly in main.py.
    telegram_bot_token: str | None = None
    telegram_admin_chat_id: int | None = None
    allowed_username: str = "sdprncss"
    allowed_user_id: int | None = None

    # AI/LLM credential. GRU_GUARDIAN_KEY belongs here and must never be logged.
    key: str | None = None
    ai_model: str = "gpt-5.6-luna"
    ai_base_url: str = "https://api.openai.com/v1"

    backend_url: str = "https://gru-jiqi.onrender.com"
    edge_url: str = "https://gru-edge-v2.onrender.com"
    health_path: str = "/actuator/health"

    render_api_key: str | None = None
    render_workspace_id: str | None = None
    render_backend_service_id: str | None = None
    render_edge_service_id: str | None = None

    github_token: str | None = None
    github_repo: str = "marie-sok/gru."
    github_release_branch: str = "release/beta-0.9.1-rc1"

    database_url: str | None = None

    poll_seconds: int = 60
    failure_threshold: int = 3
    recovery_cooldown_seconds: int = 900
    ci_poll_seconds: int = 300

    # Observe | Repair | Code | Production
    mode: str = "Observe"

    @property
    def ai_key(self) -> str | None:
        return self.key

    @property
    def can_repair(self) -> bool:
        return self.mode.lower() in {"repair", "code", "production"}

    @property
    def can_code(self) -> bool:
        return self.mode.lower() in {"code", "production"}

    @property
    def can_touch_production(self) -> bool:
        return self.mode.lower() == "production"


settings = Settings()
