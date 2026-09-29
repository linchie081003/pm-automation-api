"""Unified planned/actual/SPI for health and S-curve."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgressSnapshot, ProgressSnapshotSource, Project
from app.services.progress import resolve_actual_progress
from app.services.report_calendar import active_open_report_date, resolve_report_period
from app.services.schedule import compute_spi, planned_pct_as_of
from app.services.schedule_window import kickoff_milestones, project_report_start_date


def snapshot_for_anchor_week(
    db: Session, project_id: int, anchor: date
) -> ProgressSnapshot | None:
    return db.scalar(
        select(ProgressSnapshot).where(
            ProgressSnapshot.project_id == project_id,
            ProgressSnapshot.week_start == anchor,
        )
    )


def active_report_week_context(
    project: Project,
    *,
    as_of: date | None = None,
    db: Session | None = None,
) -> tuple[date, date, date, date]:
    """
    Minggu laporan aktif: (report_date, period_start, status_date, metrics_as_of).
    status_date == report_date (trailing).
    """
    today = date.today()
    explicit = as_of is not None
    ref = as_of or today
    rd_ref = active_open_report_date(ref, project.weekly_report_anchor_weekday)
    pstart = project_report_start_date(db, project) if db else None
    report_date, period_start, status_date = resolve_report_period(
        project.weekly_report_anchor_weekday,
        project.weekly_report_cutoff_offset_days,
        rd_ref,
        ref,
        project_start_date=pstart,
    )
    metrics_as_of = ref if explicit else min(today, status_date)
    return report_date, period_start, status_date, metrics_as_of


def delivery_week_metrics(
    db: Session,
    project: Project,
    *,
    as_of: date | None = None,
) -> tuple[float, float, date, date, str]:
    """
    Target vs actual untuk minggu laporan aktif (atau minggu as_of jika diberikan).
    Returns planned, actual, status_date (cut-off), anchor, health_source.
    """
    report_date, _, status_date, metrics_as_of = active_report_week_context(
        project, as_of=as_of, db=db
    )
    cut_off = status_date
    anchor = report_date
    plan_rows = kickoff_milestones(db, project.id)
    if not plan_rows:
        from app.services.schedule import planned_progress_rows

        plan_rows = planned_progress_rows(db, project.id)

    snap = snapshot_for_anchor_week(db, project.id, anchor)
    frozen_sources = {
        ProgressSnapshotSource.weekly_report,
        ProgressSnapshotSource.manual_save,
    }
    in_progress = as_of is None and metrics_as_of < cut_off

    if snap and snap.source in frozen_sources and not in_progress:
        planned = snap.planned_cumulative_pct
        actual = snap.actual_cumulative_pct
        source = "weekly_snapshot"
    elif snap and snap.source == ProgressSnapshotSource.planned_target:
        planned = snap.planned_cumulative_pct
        actual = resolve_actual_progress(db, project, metrics_as_of)
        source = "active_week_live"
    else:
        planned = planned_pct_as_of(plan_rows, cut_off, db=db)
        actual = resolve_actual_progress(db, project, metrics_as_of)
        source = "active_week_live" if in_progress else "live"

    return planned, actual, cut_off, anchor, source


def latest_weekly_snapshot(db: Session, project_id: int) -> ProgressSnapshot | None:
    snap = db.scalar(
        select(ProgressSnapshot)
        .where(
            ProgressSnapshot.project_id == project_id,
            ProgressSnapshot.source == ProgressSnapshotSource.weekly_report,
        )
        .order_by(ProgressSnapshot.week_start.desc())
        .limit(1)
    )
    if snap:
        return snap
    return db.scalar(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.week_start.desc())
        .limit(1)
    )


def snapshot_at_or_before(
    db: Session, project_id: int, as_of: date
) -> ProgressSnapshot | None:
    snaps = db.scalars(
        select(ProgressSnapshot)
        .where(
            ProgressSnapshot.project_id == project_id,
            ProgressSnapshot.week_start <= as_of,
        )
        .order_by(ProgressSnapshot.week_start.desc())
    ).all()
    return snaps[0] if snaps else None


def progress_as_of(
    db: Session,
    project: Project,
    as_of: date,
    *,
    plan_rows: list | None = None,
) -> tuple[float, float, float | None]:
    """Returns planned_pct, actual_pct, spi (None if planned<=0)."""
    if plan_rows is None:
        plan_rows = kickoff_milestones(db, project.id)
        if not plan_rows:
            from app.services.schedule import planned_progress_rows

            plan_rows = planned_progress_rows(db, project.id)

    planned = planned_pct_as_of(plan_rows, as_of, db=db)
    actual = resolve_actual_progress(db, project, as_of)
    spi = compute_spi(actual, planned) if planned > 0 else None
    return planned, actual, spi


def scurve_point_metrics(
    db: Session,
    project: Project,
    anchor: date,
    cut_off: date,
    plan_rows: list,
    snap_by_week: dict[date, ProgressSnapshot],
) -> tuple[float, float, float | None, bool]:
    if anchor in snap_by_week:
        s = snap_by_week[anchor]
        spi = s.spi_at_week if s.planned_cumulative_pct > 0 else None
        return s.planned_cumulative_pct, s.actual_cumulative_pct, spi, True

    snap = snapshot_at_or_before(db, project.id, cut_off)
    if snap:
        spi = snap.spi_at_week if snap.planned_cumulative_pct > 0 else None
        return snap.planned_cumulative_pct, snap.actual_cumulative_pct, spi, False

    planned, actual, spi = progress_as_of(db, project, cut_off, plan_rows=plan_rows)
    return planned, actual, spi, False
