import json
import shutil
from pathlib import Path

from app.config import settings


def manifest_path() -> Path:
    return settings.templates_path / "manifest.json"


def load_manifest() -> dict[str, str]:
    path = manifest_path()
    if not path.is_file():
        return {
            "weekly_report.xlsx": "weekly_report.xlsx",
            "weekly_report.pptx": "weekly_report.pptx",
            "pre_kickoff_deck.pptx": "pre_kickoff_deck.pptx",
            "kickoff_deck.pptx": "kickoff_deck.pptx",
            "task_export.xlsx": "task_export.xlsx",
        }
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_template(logical_key: str) -> Path:
    manifest = load_manifest()
    filename = manifest.get(logical_key, logical_key)
    path = settings.templates_path / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Template '{logical_key}' not found at {path}. "
            f"Copy org templates to {settings.templates_path}"
        )
    return path


def copy_template(logical_key: str, dest: Path) -> Path:
    src = resolve_template(logical_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
