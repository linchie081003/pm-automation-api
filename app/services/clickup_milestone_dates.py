"""Guard ClickUp date fields vs PM-confirmed PDC milestone baseline."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ClickUpTaskCache,
    Milestone,
    Project,
    ScheduleBaselineMilestone,
    TimelineItemType,
)
from app.services.schedule import get_current_baseline


def timeline_baseline_locked(project: Project | None) -> bool:
    return bool(project and project.kickoff_timeline_confirmed_at)


def authoritative_milestone_dates(
    db: Session,
    project: Project,
    milestone: Milestone,
) -> tuple[date | None, date | None]:
    """
    Tanggal acuan PDC untuk validasi ClickUp: baris baseline current (jika ada),
    else tanggal milestone live.
    """
    start = milestone.start_date
    end = milestone.target_date
    if not timeline_baseline_locked(project):
        return start, end
    baseline = get_current_baseline(db, project.id)
    if not baseline:
        return start, end
    row = db.scalar(
        select(ScheduleBaselineMilestone)
        .where(
            ScheduleBaselineMilestone.baseline_id == baseline.id,
            ScheduleBaselineMilestone.name == milestone.name,
            ScheduleBaselineMilestone.sort_order == milestone.sort_order,
            ScheduleBaselineMilestone.item_type == milestone.item_type,
        )
        .limit(1)
    )
    if not row:
        return start, end
    if row.start_date or row.target_date:
        return row.start_date or start, row.target_date or end
    return start, end


def is_clickup_subtask_cache(
    cache: ClickUpTaskCache,
    milestone: Milestone | None = None,
) -> bool:
    if cache.parent_task_id:
        return True
    if milestone and milestone.parent_id and milestone.item_type == TimelineItemType.subtask:
        return True
    return False


def guarded_dates_for_milestone_apply(
    db: Session,
    project: Project,
    milestone: Milestone,
    cache: ClickUpTaskCache | None,
    clickup_start: date | None,
    clickup_end: date | None,
) -> tuple[date | None, date | None]:
    """
    Tanggal yang boleh ditulis ke Milestone saat sync ClickUp → timeline.
    Subtask / inherit parent: caller menangani terpisah.
    Task/phase top-level ter-link: baseline PM menang; geseran ClickUp diabaikan.
    """
    if cache and is_clickup_subtask_cache(cache, milestone):
        return clickup_start, clickup_end
    if not timeline_baseline_locked(project):
        return (
            clickup_start or milestone.start_date,
            clickup_end or milestone.target_date,
        )
    base_start, base_end = authoritative_milestone_dates(db, project, milestone)
    if base_start or base_end:
        return base_start, base_end
    return (
        clickup_start or milestone.start_date,
        clickup_end or milestone.target_date,
    )


def rollup_milestone_dates_from_children(
    milestone_id: int,
    children_map: dict[int, list[Milestone]],
) -> tuple[date | None, date | None]:
    from app.services.milestone_schedule_rollup import rollup_span_from_children

    kids = children_map.get(milestone_id, [])
    return rollup_span_from_children(kids)


def raw_clickup_cache_allowed_for_linked_task(
    project: Project | None,
    cache: ClickUpTaskCache,
    linked: Milestone | None,
    *,
    is_subtask: bool,
) -> bool:
    """Non-subtask ter-link ke milestone: jangan pakai start/due mentah ClickUp bila baseline terkunci."""
    if is_subtask:
        return True
    if not linked:
        return True
    if not timeline_baseline_locked(project):
        return True
    base_start, base_end = linked.start_date, linked.target_date
    if base_start or base_end:
        return False
    return not cache.parent_task_id
