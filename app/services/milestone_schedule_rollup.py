"""Bottom-up date rollup for live milestones and draft baseline rows."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Milestone, Project, TimelineItemType
from app.services.business_calendar import count_business_days_inclusive

ROLLUP_CHILD_TYPES = frozenset(
    {
        TimelineItemType.phase,
        TimelineItemType.milestone,
        TimelineItemType.task,
        TimelineItemType.subtask,
    }
)


class _SchedRow(Protocol):
    id: int
    parent_id: int | None
    item_type: TimelineItemType
    start_date: Any
    target_date: Any
    duration_days: int | None
    sort_order: int


def _rollup_children(rows: list[_SchedRow]) -> list[_SchedRow]:
    return [r for r in rows if r.item_type in ROLLUP_CHILD_TYPES]


def rollup_span_from_children(
    children: list[_SchedRow],
) -> tuple[Any | None, Any | None]:
    """min(start) / max(target) dari anak phase/milestone/task/subtask."""
    weighted = _rollup_children(children)
    starts = [c.start_date for c in weighted if c.start_date]
    ends = [c.target_date for c in weighted if c.target_date]
    if starts and ends:
        return min(starts), max(ends)
    return None, None


def rollup_container_dates_bottom_up(
    rows: list[_SchedRow],
    db: Session | None,
    *,
    by_id: dict[int, _SchedRow] | None = None,
) -> int:
    """
    Phase/task dengan anak: start = min(anak.start), target = max(anak.target).
    Sama dengan pass bottom-up di schedule_draft_milestone_rows().
    """
    if not rows:
        return 0
    lookup = by_id or {r.id: r for r in rows}
    depth_cache: dict[int, int] = {}

    def depth(rid: int) -> int:
        if rid in depth_cache:
            return depth_cache[rid]
        r = lookup.get(rid)
        if not r or not r.parent_id or r.parent_id not in lookup:
            depth_cache[rid] = 0
            return 0
        depth_cache[rid] = depth(r.parent_id) + 1
        return depth_cache[rid]

    sched = _rollup_children(list(rows))
    updated = 0
    for row in sorted(sched, key=lambda x: (-depth(x.id), x.sort_order, x.id)):
        kids = sorted(
            [r for r in lookup.values() if r.parent_id == row.id],
            key=lambda x: (x.sort_order, x.id),
        )
        start, end = rollup_span_from_children(kids)
        if not start or not end:
            continue
        changed = False
        if row.start_date != start:
            row.start_date = start
            changed = True
        if row.target_date != end:
            row.target_date = end
            changed = True
        dur = count_business_days_inclusive(start, end, db)
        if row.duration_days != dur:
            row.duration_days = dur
            changed = True
        if changed:
            updated += 1
    return updated


def rollup_live_milestone_dates(db: Session, project_id: int) -> int:
    milestones = list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.id)
        ).all()
    )
    if not milestones:
        return 0
    by_id = {m.id: m for m in milestones}
    updated = rollup_container_dates_bottom_up(milestones, db, by_id=by_id)
    project = db.get(Project, project_id)
    if project and updated:
        work = [m for m in milestones if m.item_type in ROLLUP_CHILD_TYPES]
        starts = [m.start_date for m in work if m.start_date]
        targets = [m.target_date for m in work if m.target_date]
        if starts:
            project.planned_start_date = min(starts)
        if targets:
            project.planned_end_date = max(targets)
    return updated


def milestone_date_anomalies(milestones: list[Milestone]) -> dict[int, list[str]]:
    """
    Red flag: anak mulai sebelum parent atau target anak melewati target parent.
    """
    by_id = {m.id: m for m in milestones}
    out: dict[int, list[str]] = {}

    def add(mid: int, msg: str) -> None:
        out.setdefault(mid, []).append(msg)

    for m in milestones:
        pid = m.parent_id
        if not pid or pid not in by_id:
            continue
        parent = by_id[pid]
        if m.start_date and parent.start_date and m.start_date < parent.start_date:
            add(
                m.id,
                f"Mulai {m.start_date.isoformat()} sebelum parent ({parent.start_date.isoformat()})",
            )
            add(
                pid,
                f"Rentang tidak membungkus anak «{m.name}» (mulai terlalu awal)",
            )
        if m.target_date and parent.target_date and m.target_date > parent.target_date:
            add(
                m.id,
                f"Target {m.target_date.isoformat()} setelah parent ({parent.target_date.isoformat()})",
            )
            add(
                pid,
                f"Rentang tidak membungkus anak «{m.name}» (target terlalu akhir)",
            )
    return out
