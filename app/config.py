from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent

_INSECURE_JWT_SECRETS = frozenset(
    {
        "change-me-in-production",
        "change-me",
        "secret",
        "jwt-secret",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        extra="ignore",
    )

    database_url: str = "postgresql://pdc:pdc@127.0.0.1:5432/pdc"
    jwt_secret: str = "change-me-in-production"
    # development | production
    app_env: str = "development"
    # Comma-separated browser origins (required when SPA calls API cross-origin).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300
    upload_dir: str = "uploads"
    # Path to Google service account JSON (share each project folder with client_email as Editor).
    google_drive_service_account_file: str = ""
    template_dir: str = ""
    clickup_enabled: bool = False
    skip_template_verify: bool = False
    seed_timeline_templates_on_startup: bool = False
    # v1 = legacy schedule_draft_milestone_rows; v2 = recalc_timeline_editor_rows
    #timeline_engine: str = "v1"
    timeline_engine: str = "v2"

    @property
    def templates_path(self) -> Path:
        if self.template_dir:
            return Path(self.template_dir)
        return _BACKEND_DIR / "templates"

    @property
    def cors_origins_list(self) -> list[str]:
        parts = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return parts or ["http://localhost:5173"]

    @property
    def jwt_secret_is_weak(self) -> bool:
        s = self.jwt_secret.strip()
        return s.lower() in _INSECURE_JWT_SECRETS or len(s) < 32

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, v: str) -> str:
        return (v or "development").strip().lower()

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_cors(cls, v: str) -> str:
        raw = (v or "").strip()
        if not raw:
            return raw
        for part in raw.split(","):
            origin = part.strip()
            if origin == "*":
                raise ValueError(
                    "CORS_ORIGINS must not use '*' with HttpOnly cookie auth — "
                    "list explicit origins (e.g. http://localhost:5173)"
                )
        return raw

    @model_validator(mode="after")
    def require_strong_jwt_in_production(self) -> Self:
        if self.app_env == "production" and self.jwt_secret_is_weak:
            raise ValueError(
                "JWT_SECRET must be set to a random string of at least 32 characters "
                "when APP_ENV=production"
            )
        return self


settings = Settings()
