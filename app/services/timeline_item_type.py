"""Normalize timeline item_type strings from templates and API."""
from app.models import TimelineItemType


def parse_timeline_item_type(raw: str | None) -> TimelineItemType:
    if not raw:
        return TimelineItemType.phase
    key = str(raw).strip().lower()
    try:
        return TimelineItemType(key)
    except ValueError:
        if key in ("milestone",):
            return TimelineItemType.milestone
        return TimelineItemType.phase
