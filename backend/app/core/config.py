"""Application configuration via pydantic-settings (12-factor env).

Local-mode defaults are chosen so the service boots with no infra: SQLite DB,
local filesystem storage, and inline task execution. Switch to Postgres/S3/Celery
by overriding these in ``.env`` when running the Docker stack.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Look for .env in both the backend/ working dir and the repo root (one
    # level up) so the process works regardless of where it's launched from.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174"

    # Anthropic mode:
    #   "agent" — route calls through the Claude Agent SDK (uses the local
    #             ``claude`` CLI's OAuth / Max subscription — no API key).
    #   "real"  — hit the HTTP Anthropic API with ANTHROPIC_API_KEY.
    #   "mock"  — canned responses, no external calls.
    #   "auto"  — prefer agent if available, else real if key set, else mock.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODE: Literal["auto", "mock", "real", "agent"] = "auto"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_HAIKU_MODEL: str = "claude-haiku-4-5"

    @property
    def anthropic_effective_mode(self) -> str:
        if self.ANTHROPIC_MODE in ("agent", "real", "mock"):
            return self.ANTHROPIC_MODE
        # auto
        try:
            from app.core.claude_agent import is_available as _agent_available

            if _agent_available():
                return "agent"
        except Exception:
            pass
        try:
            from app.core.claude_client import has_real_key

            if has_real_key():
                return "real"
        except Exception:
            pass
        return "mock"

    @property
    def anthropic_use_mock(self) -> bool:
        return self.anthropic_effective_mode == "mock"

    @property
    def anthropic_use_agent(self) -> bool:
        return self.anthropic_effective_mode == "agent"

    # Database — defaults to SQLite under ./cmds.db for zero-infra local runs
    DATABASE_URL: str = "sqlite+aiosqlite:///./cmds.db"
    SYNC_DATABASE_URL: str = "sqlite:///./cmds.db"

    # Task executor — "inline" runs tasks in-process (no Celery needed);
    # "celery" dispatches to a worker over Redis.
    TASK_EXECUTOR: Literal["inline", "celery"] = "inline"

    # Redis / Celery (only used when TASK_EXECUTOR=celery)
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Storage — local filesystem by default; switch to "s3" for MinIO/S3
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_ROOT: str = "./storage"
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_PUBLIC_ENDPOINT: str = "http://localhost:8001"  # backend serves /storage in local mode
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_PDFS: str = "cmds-pdfs"
    S3_BUCKET_EXPORTS: str = "cmds-exports"
    S3_BUCKET_OCR_UPLOADS: str = "cmds-ocr-uploads"

    # Crypto — required to be set (Fernet). Bootstrap script generates one.
    ENCRYPTION_KEY: str = ""

    # Gemini — used for PDF extraction and regeneration
    GEMINI_API_KEY: str = ""
    GEMINI_REGEN_MODEL: str = "gemini-2.5-pro"

    # Question worker version. v2 = legacy excluded-block-driven (3-pass, has
    # the cross-section-duplication problem). v3 = section-aligned (mirrors
    # theory extractor; one Gemini call per schema section). Default v3.
    QUESTION_WORKER_VERSION: Literal["v2", "v3"] = "v3"

    # Multi-OCR (Sprint 4; empty by default)
    MATHPIX_APP_ID: str = ""
    MATHPIX_APP_KEY: str = ""
    SARVAM_API_KEY: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
