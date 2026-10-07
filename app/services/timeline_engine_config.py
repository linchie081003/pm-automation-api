"""Feature flag: v1 = schedule_draft_milestone_rows, v2 = timeline editor engine."""

from app.config import settings


def use_timeline_engine_v2() -> bool:
    raw = (getattr(settings, "timeline_engine", None) or "v1").strip().lower()
    return raw in ("v2", "2", "beta", "editor")
