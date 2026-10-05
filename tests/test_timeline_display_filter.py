from app.models import TimelineItemType
from app.services.timeline_display import filter_timeline_display_rows


def test_filter_timeline_display_rows_drops_subtasks_by_default():
    rows = [
        {"item_type": TimelineItemType.phase.value, "name": "P1"},
        {"item_type": TimelineItemType.task.value, "name": "T1"},
        {"item_type": TimelineItemType.subtask.value, "name": "S1"},
    ]
    out = filter_timeline_display_rows(rows)
    assert len(out) == 2
    assert out[1]["name"] == "T1"


def test_filter_timeline_display_rows_include_subtasks():
    rows = [{"item_type": TimelineItemType.subtask.value, "name": "S1"}]
    assert len(filter_timeline_display_rows(rows, include_subtasks=True)) == 1
