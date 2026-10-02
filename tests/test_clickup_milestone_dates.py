from datetime import date
from unittest.mock import MagicMock

from app.core.timezone import now_jakarta
from app.models import TimelineItemType
from app.services.clickup_milestone_dates import (
    guarded_dates_for_milestone_apply,
    raw_clickup_cache_allowed_for_linked_task,
    rollup_milestone_dates_from_children,
    timeline_baseline_locked,
)

ITEM_SUB = TimelineItemType.subtask


def test_timeline_baseline_locked_after_kickoff_confirm():
    p = MagicMock()
    p.kickoff_timeline_confirmed_at = now_jakarta()
    assert timeline_baseline_locked(p) is True
    p.kickoff_timeline_confirmed_at = None
    assert timeline_baseline_locked(p) is False


def test_guarded_dates_keep_pdc_when_clickup_shifts():
    project = MagicMock()
    project.id = 1
    project.kickoff_timeline_confirmed_at = now_jakarta()

    milestone = MagicMock()
    milestone.start_date = date(2026, 3, 1)
    milestone.target_date = date(2026, 3, 15)
    milestone.parent_id = None
    milestone.item_type = TimelineItemType.task
    milestone.name = "Task A"
    milestone.sort_order = 1

    cache = MagicMock()
    cache.parent_task_id = None

    db = MagicMock()
    db.scalar.return_value = None

    start, end = guarded_dates_for_milestone_apply(
        db,
        project,
        milestone,
        cache,
        date(2026, 3, 10),
        date(2026, 3, 20),
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 15)


def test_guarded_dates_allow_clickup_before_kickoff_confirm():
    project = MagicMock()
    project.kickoff_timeline_confirmed_at = None

    milestone = MagicMock()
    milestone.start_date = date(2026, 3, 1)
    milestone.target_date = date(2026, 3, 15)
    milestone.parent_id = None
    milestone.item_type = TimelineItemType.task

    cache = MagicMock()
    cache.parent_task_id = None

    start, end = guarded_dates_for_milestone_apply(
        MagicMock(),
        project,
        milestone,
        cache,
        date(2026, 3, 10),
        date(2026, 3, 20),
    )
    assert start == date(2026, 3, 10)
    assert end == date(2026, 3, 20)


def test_rollup_parent_span_from_linked_subtasks():
    parent_id = 10
    sub_a = MagicMock()
    sub_a.clickup_task_id = "cu-a"
    sub_a.item_type = ITEM_SUB
    sub_a.start_date = date(2026, 4, 1)
    sub_a.target_date = date(2026, 4, 8)
    sub_b = MagicMock()
    sub_b.item_type = ITEM_SUB
    sub_b.clickup_task_id = "cu-b"
    sub_b.start_date = date(2026, 4, 5)
    sub_b.target_date = date(2026, 4, 12)
    children_map = {parent_id: [sub_a, sub_b]}
    start, end = rollup_milestone_dates_from_children(parent_id, children_map)
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 12)


def test_raw_clickup_blocked_for_linked_task_when_baseline_set():
    project = MagicMock()
    project.kickoff_timeline_confirmed_at = now_jakarta()
    linked = MagicMock()
    linked.start_date = date(2026, 1, 1)
    linked.target_date = date(2026, 1, 10)
    cache = MagicMock()
    cache.parent_task_id = None
    assert (
        raw_clickup_cache_allowed_for_linked_task(
            project, cache, linked, is_subtask=False
        )
        is False
    )
