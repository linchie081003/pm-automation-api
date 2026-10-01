from datetime import date
from types import SimpleNamespace

from app.models import TimelineItemType
from app.services.timeline_schedule import schedule_draft_milestone_rows


def _mk(
    *,
    id: int,
    row_key: str,
    name: str,
    item_type: TimelineItemType,
    parent_id: int | None = None,
    sort_order: int = 0,
    duration_days: int = 3,
    predecessor_ref: str | None = None,
    predecessor_link_type: str | None = None,
):
    return SimpleNamespace(
        id=id,
        row_key=row_key,
        name=name,
        item_type=item_type,
        parent_id=parent_id,
        sort_order=sort_order,
        duration_days=duration_days,
        predecessor_ref=predecessor_ref,
        predecessor_link_type=predecessor_link_type,
        start_date=None,
        target_date=None,
        weight_pct=0.0,
    )


def test_schedule_task_chain_and_duration_driver(monkeypatch):
    def fake_after(d, db=None):
        return date(2026, 10, 6)

    def fake_add(start, days, db=None):
        if days == 1:
            return start
        return date(2026, 10, 8)

    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        fake_after,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.count_business_days_inclusive",
        lambda s, e, db=None: 3,
    )

    phase = _mk(id=1, row_key="p1", name="Phase", item_type=TimelineItemType.phase, sort_order=0)
    t1 = _mk(
        id=2,
        row_key="t1",
        name="Task 1",
        item_type=TimelineItemType.task,
        parent_id=1,
        sort_order=1,
        duration_days=2,
    )
    t2 = _mk(
        id=3,
        row_key="t2",
        name="Task 2",
        item_type=TimelineItemType.task,
        parent_id=1,
        sort_order=2,
        duration_days=2,
        predecessor_ref="t1",
        predecessor_link_type="FS",
    )
    schedule_draft_milestone_rows(None, [phase, t1, t2], date(2026, 10, 1))
    assert t1.start_date is not None
    assert t2.start_date == date(2026, 10, 6)
