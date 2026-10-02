from datetime import date

from app.models import TimelineItemType
from app.services.timeline_report_metrics import (
    display_row_planned_fraction,
    display_row_planned_week_fraction,
    display_row_weight_fraction,
    include_in_report_timeline_list,
)


def test_report_timeline_list_includes_phase_milestone_task_only():
    assert include_in_report_timeline_list({"item_type": TimelineItemType.phase.value})
    assert include_in_report_timeline_list({"item_type": TimelineItemType.milestone.value})
    assert include_in_report_timeline_list({"item_type": TimelineItemType.task.value})
    assert not include_in_report_timeline_list({"item_type": TimelineItemType.subtask.value})
    assert not include_in_report_timeline_list({"item_type": ""})


def test_display_row_weight_uses_weight_pct():
    row = {"weight_pct": 46.25}
    assert display_row_weight_fraction(row) == 0.4625


def test_display_row_planned_fraction_at_cutoff():
    row = {
        "start_date": "2026-03-02",
        "target_date": "2026-03-06",
        "weight_pct": 50,
    }
    frac = display_row_planned_fraction(row, date(2026, 3, 6), db=None)
    assert frac == 1.0


def test_display_row_planned_week_fraction_overlap():
    row = {
        "start_date": "2026-03-02",
        "target_date": "2026-03-06",
        "weight_pct": 100,
    }
    # Mon–Fri week fully inside phase
    share = display_row_planned_week_fraction(
        row, 1.0, date(2026, 3, 2), date(2026, 3, 6), db=None
    )
    assert round(share, 4) == 1.0
