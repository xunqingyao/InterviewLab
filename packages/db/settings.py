from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://interviewlab:interviewlab@127.0.0.1:5432/interviewlab"
    interviewlab_dev_auth: bool = True
    dev_user_display_name: str = "local-dev"
    session_secret: str = "change-me-in-any-non-local-env"
    public_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    file_root: str = "./data/files"
    checkpoint_schema: str = "langgraph"
    model_provider: str = "fake"
    model_api_key: str = ""
    model_api_base: str = ""
    model_identifier: str = "fake-interviewer-v1"
    max_session_cost: str = "5.00"
    max_upload_bytes: int = 5_242_880
    global_llm_slots: int = 4
    cors_enabled: bool = False
    worker_id: str = "worker-local"
    inline_worker: bool = True
    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    report_grace_seconds: int = 60
    event_retention: int = 10_000

    @property
    def origin_list(self) -> list[str]:
        return [item.strip() for item in self.public_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
