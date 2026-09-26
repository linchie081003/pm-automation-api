"""Recalculate delivery Milestone dates from project start + durations (kalender kerja).

Hanya mengubah baris Milestone (timeline delivery). Draft SPH / Kick Off tidak disentuh.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Milestone, Project, TimelineItemType
from app.services.business_calendar import add_business_days, business_day_after


def _children(by_id: dict[int, Milestone], parent_id: int) -> list[Milestone]:
    return sorted(
        [r for r in by_id.values() if r.parent_id == parent_id],
        key=lambda x: (x.sort_order, x.id),
    )


def _apply_subtask_dates(row: Milestone, start: date, db: Session) -> None:
    dur = max(row.duration_days or 1, 1)
    row.start_date = add_business_days(start, 1, db)
    row.target_date = add_business_days(row.start_date, dur, db)


def _apply_task_dates(
    row: Milestone,
    start: date,
    db: Session,
    by_id: dict[int, Milestone],
) -> None:
    subtasks = [
        c for c in _children(by_id, row.id) if c.item_type == TimelineItemType.subtask
    ]
    if subtasks:
        cursor = start
        for st in subtasks:
            _apply_subtask_dates(st, cursor, db)
            if st.target_date:
                cursor = business_day_after(st.target_date, db)
        starts = [s.start_date for s in subtasks if s.start_date]
        ends = [s.target_date for s in subtasks if s.target_date]
        row.start_date = min(starts) if starts else add_business_days(start, 1, db)
        row.target_date = max(ends) if ends else row.start_date
    else:
        dur = max(row.duration_days or 1, 1)
        row.start_date = add_business_days(start, 1, db)
        row.target_date = add_business_days(row.start_date, dur, db)


def _apply_milestone_gate(row: Milestone, default_day: date, manual: date | None) -> None:
    d = manual or default_day
    row.start_date = d
    row.target_date = d
    row.duration_days = 0


def _apply_phase_dates(
    row: Milestone,
    phase_start: date,
    db: Session,
    by_id: dict[int, Milestone],
    milestone_manual: dict[int, date | None],
) -> None:
    kids = _children(by_id, row.id)
    tasks = [c for c in kids if c.item_type == TimelineItemType.task]
    gates = [c for c in kids if c.item_type == TimelineItemType.milestone]

    phase_work_start = add_business_days(phase_start, 1, db)
    dur = max(row.duration_days or 1, 1)
    duration_end = add_business_days(phase_work_start, dur, db)

    cursor = phase_start
    for task in tasks:
        _apply_task_dates(task, cursor, db, by_id)
        if task.target_date:
            cursor = business_day_after(task.target_date, db)

    if tasks:
        last_day = max(
            (t.target_date for t in tasks if t.target_date),
            default=phase_work_start,
        )
    else:
        last_day = duration_end

    for gate in gates:
        manual = milestone_manual.get(gate.id)
        _apply_milestone_gate(gate, last_day, manual)

    span_starts: list[date] = []
    span_ends: list[date] = []
    for c in tasks + gates:
        if c.start_date:
            span_starts.append(c.start_date)
        if c.target_date:
            span_ends.append(c.target_date)

    if tasks:
        if span_starts and span_ends:
            row.start_date = min(span_starts)
            row.target_date = max(span_ends)
        else:
            row.start_date = phase_work_start
            row.target_date = duration_end
    else:
        row.start_date = phase_work_start
        row.target_date = duration_end
        if span_starts:
            row.start_date = min(row.start_date, min(span_starts))
        if span_ends:
            row.target_date = max(row.target_date, max(span_ends))


def recalc_live_timeline_dates(
    db: Session,
    project_id: int,
    start: date,
    milestone_manual: dict[int, date | None] | None = None,
) -> list[Milestone]:
    rows = list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.id)
        ).all()
    )
    if not rows:
        raise ValueError("Timeline delivery belum ada — konfirmasi Kick Off terlebih dahulu.")
    by_id = {r.id: r for r in rows}
    manual = milestone_manual or {}
    top = [
        r
        for r in rows
        if not r.parent_id
        and r.item_type in (TimelineItemType.phase, TimelineItemType.milestone)
    ]
    cursor = start
    for row in sorted(top, key=lambda x: (x.sort_order, x.id)):
        if row.item_type == TimelineItemType.milestone:
            row.item_type = TimelineItemType.phase
        _apply_phase_dates(row, cursor, db, by_id, manual)
        if row.target_date:
            cursor = business_day_after(row.target_date, db)
    return rows


def apply_project_timeline_start(db: Session, project: Project, start: date) -> dict:
    """Set tanggal start proyek (Timeline) dan geser milestone delivery saja."""
    if not project.kickoff_timeline_confirmed_at:
        raise ValueError("Timeline Kick Off belum dikonfirmasi.")
    rows = recalc_live_timeline_dates(db, project.id, start)
    project.planned_start_date = start
    targets = [m.target_date for m in rows if m.target_date]
    if targets:
        project.planned_end_date = max(targets)
    return {
        "planned_start_date": start.isoformat(),
        "planned_end_date": project.planned_end_date.isoformat()
        if project.planned_end_date
        else None,
        "milestone_count": len(rows),
    }
