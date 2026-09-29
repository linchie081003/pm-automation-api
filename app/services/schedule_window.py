"""Project schedule bounds from kickoff-confirmed milestones."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Milestone, Project, ScheduleBaselineMilestone, TimelineItemType

TIMELINE_WORK_TYPES = frozenset(
    {
        TimelineItemType.phase,
        TimelineItemType.task,
        TimelineItemType.subtask,
    }
)


def kickoff_milestones(db: Session, project_id: int) -> list[Milestone]:
    project = db.get(Project, project_id)
    if not project or not project.kickoff_timeline_confirmed_at:
        return []
    return list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.id)
        ).all()
    )


def _bounds_from_milestones(rows: list[Milestone]) -> tuple[date | None, date | None]:
    if not rows:
        return None, None
    starts = [m.start_date for m in rows if m.start_date]
    targets = [m.target_date for m in rows if m.target_date]
    if not starts and not targets:
        return None, None
    start = min(starts) if starts else min(targets)
    end = max(targets) if targets else max(starts)
    return start, end


def schedule_bounds_with_source(
    db: Session, project_id: int
) -> tuple[date | None, date | None, str]:
    rows = kickoff_milestones(db, project_id)
    start, end = _bounds_from_milestones(rows)
    if start and end:
        return start, end, "kickoff_milestones"

    all_ms = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    start, end = _bounds_from_milestones(all_ms)
    if start and end:
        return start, end, "milestones"

    from app.services.schedule import get_draft_baseline

    draft = get_draft_baseline(db, project_id)
    if draft:
        sb_rows = list(
            db.scalars(
                select(ScheduleBaselineMilestone).where(
                    ScheduleBaselineMilestone.baseline_id == draft.id
                )
            ).all()
        )
        starts = [r.start_date for r in sb_rows if r.start_date]
        targets = [r.target_date for r in sb_rows if r.target_date]
        if starts or targets:
            start = min(starts) if starts else min(targets)
            end = max(targets) if targets else max(starts)
            return start, end, "draft_baseline"

    project = db.get(Project, project_id)
    if not project:
        return None, None, "none"

    start = None
    if project.delivery_started_at:
        start = project.delivery_started_at.date()
    elif project.po_date:
        start = project.po_date

    end = project.planned_end_date or project.po_due_date
    if start and end:
        return start, end, "project_dates"
    if end and not start:
        start = end - timedelta(days=90)
        return start, end, "project_dates_inferred"
    if start and not end:
        end = start + timedelta(days=180)
        return start, end, "project_dates_inferred"

    return None, None, "none"


def schedule_bounds(db: Session, project_id: int) -> tuple[date | None, date | None]:
    start, end, _ = schedule_bounds_with_source(db, project_id)
    return start, end


def _timeline_rows_for_reporting(db: Session, project_id: int) -> list[Milestone]:
    rows = kickoff_milestones(db, project_id)
    if rows:
        return rows
    return list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )


def timeline_report_bounds(
    db: Session, project_id: int
) -> tuple[date | None, date | None, str]:
    """
    Start = tanggal start paling awal di timeline (phase/task/subtask).
    End = target_date paling akhir di timeline.
    """
    rows = _timeline_rows_for_reporting(db, project_id)
    work = [m for m in rows if m.item_type in TIMELINE_WORK_TYPES]
    pool = work if work else rows
    starts = [m.start_date for m in pool if m.start_date]
    targets = [m.target_date for m in pool if m.target_date]
    if starts or targets:
        start = min(starts) if starts else min(targets)
        end = max(targets) if targets else max(starts)
        confirmed = bool(kickoff_milestones(db, project_id))
        return start, end, "kickoff_milestones" if confirmed else "milestones"
    return schedule_bounds_with_source(db, project_id)


def project_report_start_date(db: Session, project: Project | int) -> date | None:
    """Mulai weekly report / S-curve = tanggal start proyek (input Timeline)."""
    if isinstance(project, int):
        project_id = project
        p = db.get(Project, project_id)
    else:
        p = project
        project_id = project.id
    if p and p.planned_start_date:
        return p.planned_start_date
    start, _, _ = timeline_report_bounds(db, project_id)
    return start


def project_report_end_date(db: Session, project_id: int) -> tuple[date | None, str]:
    """Akhir rentang anchor = selesai task/phase paling akhir di timeline."""
    _, end, source = timeline_report_bounds(db, project_id)
    return end, source


def reports_scurve_available(db: Session, project: Project) -> bool:
    """Tampilkan S-curve setelah timeline kick off atau target weekly report di-generate."""
    from app.models import ProgressSnapshot, ProgressSnapshotSource

    if project.kickoff_timeline_confirmed_at:
        return True
    if scurve_allowed(project):
        return True
    snap = db.scalar(
        select(ProgressSnapshot.id)
        .where(
            ProgressSnapshot.project_id == project.id,
            ProgressSnapshot.source == ProgressSnapshotSource.planned_target,
        )
        .limit(1)
    )
    return snap is not None


def scurve_allowed(project: Project) -> bool:
    from app.models import ProjectPhase

    if not project.delivery_started_at:
        return False
    return project.current_phase in (
        ProjectPhase.in_delivery,
        ProjectPhase.bast,
        ProjectPhase.closed,
    )


def project_health_metrics_enabled(project: Project) -> bool:
    """Planned/actual/RAG (status date) — delivery atau timeline Kick Off sudah dikonfirmasi."""
    if scurve_allowed(project):
        return True
    return bool(project.kickoff_timeline_confirmed_at)
