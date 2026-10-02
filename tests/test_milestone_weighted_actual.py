from datetime import date
from types import SimpleNamespace

from app.models import MilestoneStatus, TimelineItemType
from app.services.progress import milestone_weighted_actual_pct
from app.services.schedule import actual_pct_as_of


def _m(weight, done: bool, actual: date | None):
    return SimpleNamespace(
        item_type=TimelineItemType.task,
        weight_pct=weight,
        status=MilestoneStatus.done if done else MilestoneStatus.open,
        actual_date=actual,
        sort_order=0,
        id=1,
    )


def test_actual_pct_as_of_delegates_to_milestone_weighted():
    rows = [_m(60, True, date(2026, 1, 10)), _m(40, False, None)]
    assert actual_pct_as_of(rows, date(2026, 1, 15)) == milestone_weighted_actual_pct(
        rows, date(2026, 1, 15)
    )
    assert actual_pct_as_of(rows, date(2026, 1, 15)) == 60.0
