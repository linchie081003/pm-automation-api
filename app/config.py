from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        extra="ignore",
    )

    database_url: str = "postgresql://pdc:pdc@127.0.0.1:5432/pdc"
    jwt_secret: str = "change-me-in-production"
    upload_dir: str = "uploads"
    template_dir: str = ""
    clickup_enabled: bool = False
    skip_template_verify: bool = False
    seed_timeline_templates_on_startup: bool = False

    @property
    def templates_path(self) -> Path:
        if self.template_dir:
            return Path(self.template_dir)
        return _BACKEND_DIR / "templates"


settings = Settings()
