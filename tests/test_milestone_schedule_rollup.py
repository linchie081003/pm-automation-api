from datetime import date
from types import SimpleNamespace

from app.models import TimelineItemType
from app.services.milestone_schedule_rollup import (
    milestone_date_anomalies,
    rollup_container_dates_bottom_up,
    rollup_span_from_children,
)


def _row(rid, pid, start, end, it=TimelineItemType.task):
    return SimpleNamespace(
        id=rid,
        parent_id=pid,
        item_type=it,
        start_date=start,
        target_date=end,
        duration_days=5,
        sort_order=rid,
    )


def test_rollup_span_from_children_includes_milestone_gates():
    gate = _row(10, 1, date(2026, 11, 25), date(2026, 11, 25), TimelineItemType.milestone)
    start, end = rollup_span_from_children([gate])
    assert start == date(2026, 11, 25)
    assert end == date(2026, 11, 25)


def test_rollup_span_from_children_min_max():
    phase = _row(1, None, date(2026, 1, 1), date(2026, 12, 31), TimelineItemType.phase)
    t1 = _row(2, 1, date(2026, 3, 1), date(2026, 3, 10))
    t2 = _row(3, 1, date(2026, 4, 1), date(2026, 4, 15))
    start, end = rollup_span_from_children([t1, t2])
    assert start == date(2026, 3, 1)
    assert end == date(2026, 4, 15)
    by_id = {r.id: r for r in [phase, t1, t2]}
    rollup_container_dates_bottom_up([phase, t1, t2], None, by_id=by_id)
    assert phase.start_date == date(2026, 3, 1)
    assert phase.target_date == date(2026, 4, 15)


def test_milestone_date_anomalies_flags_child_and_parent():
    from app.models import Milestone

    parent = Milestone(
        id=1,
        project_id=1,
        name="Phase A",
        start_date=date(2026, 5, 1),
        target_date=date(2026, 5, 20),
        weight_pct=50,
    )
    child = Milestone(
        id=2,
        project_id=1,
        name="Task X",
        parent_id=1,
        start_date=date(2026, 4, 28),
        target_date=date(2026, 5, 25),
        weight_pct=10,
    )
    anom = milestone_date_anomalies([parent, child])
    assert 2 in anom
    assert 1 in anom
    assert any("sebelum parent" in m for m in anom[2])
    assert any("tidak membungkus" in m for m in anom[1])
