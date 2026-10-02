from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import (
    ProgressSnapshot,
    ProgressSnapshotSource,
    Project,
    ScheduleBaseline,
    ScheduleBaselineMilestone,
    User,
)
from app.services.activity import log_activity
from app.schemas.schedule import (
    BaselineMilestoneOut,
    BaselineOut,
    ProgressSnapshotOut,
    RebaselineMarker,
    RebaselineRequest,
    SaveWeekResponse,
    ScurvePoint,
)
from app.services.schedule import (
    milestone_chart_points,
    rebaseline_markers,
    save_weekly_progress,
    scurve_points,
    seed_planned_weekly_targets,
    snapshot_anchor_for_date,
)

router = APIRouter(prefix="/projects/{project_id}/schedule", tags=["schedule"])


class BaselineCompareRow(BaseModel):
    name: str
    milestone_id: int | None
    from_target: date | None
    to_target: date | None
    from_weight: float
    to_weight: float
    target_changed: bool
    weight_changed: bool


class BaselineCompareOut(BaseModel):
    from_version: int
    to_version: int
    rows: list[BaselineCompareRow]


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _snapshot_out(s: ProgressSnapshot) -> ProgressSnapshotOut:
    return ProgressSnapshotOut(
        id=s.id,
        week_start=s.week_start,
        week_end=s.week_end,
        baseline_version=s.baseline_version,
        planned_cumulative_pct=s.planned_cumulative_pct,
        actual_cumulative_pct=s.actual_cumulative_pct,
        spi_at_week=s.spi_at_week,
        source=s.source.value,
        created_at=s.created_at.isoformat(),
    )


def _baseline_out(db: Session, b: ScheduleBaseline) -> BaselineOut:
    rows = db.scalars(
        select(ScheduleBaselineMilestone).where(
            ScheduleBaselineMilestone.baseline_id == b.id
        )
    ).all()
    return BaselineOut(
        id=b.id,
        version=b.version,
        effective_from=b.effective_from,
        reason=b.reason,
        is_current=b.is_current,
        is_draft=b.is_draft,
        created_at=b.created_at.isoformat(),
        milestones=[BaselineMilestoneOut.model_validate(r) for r in rows],
    )


def _auth_read(project_id: int, user: User, codes: set[str], db: Session) -> None:
    ensure_permission(codes, "schedule.read")
    ensure_project_read(project_id, user, codes, db)
    _get_project(db, project_id)


@router.get("/baselines", response_model=list[BaselineOut])
def list_baselines(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    baselines = db.scalars(
        select(ScheduleBaseline)
        .where(ScheduleBaseline.project_id == project_id)
        .order_by(ScheduleBaseline.version)
    ).all()
    return [_baseline_out(db, b) for b in baselines]


@router.get("/baselines/compare", response_model=BaselineCompareOut)
def compare_baselines(
    project_id: int,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)

    def load(version: int) -> dict[tuple[str, int | None], ScheduleBaselineMilestone]:
        bl = db.scalar(
            select(ScheduleBaseline).where(
                ScheduleBaseline.project_id == project_id,
                ScheduleBaseline.version == version,
            )
        )
        if not bl:
            raise HTTPException(status_code=404, detail=f"Baseline v{version} not found")
        rows = db.scalars(
            select(ScheduleBaselineMilestone).where(
                ScheduleBaselineMilestone.baseline_id == bl.id
            )
        ).all()
        return {(r.name, r.milestone_id): r for r in rows}

    a = load(from_version)
    b = load(to_version)
    keys = set(a.keys()) | set(b.keys())
    rows: list[BaselineCompareRow] = []
    for key in sorted(keys, key=lambda k: k[0]):
        ra, rb = a.get(key), b.get(key)
        rows.append(
            BaselineCompareRow(
                name=key[0],
                milestone_id=key[1],
                from_target=ra.target_date if ra else None,
                to_target=rb.target_date if rb else None,
                from_weight=ra.weight_pct if ra else 0,
                to_weight=rb.weight_pct if rb else 0,
                target_changed=(ra.target_date if ra else None)
                != (rb.target_date if rb else None),
                weight_changed=(ra.weight_pct if ra else 0)
                != (rb.weight_pct if rb else 0),
            )
        )
    return BaselineCompareOut(
        from_version=from_version, to_version=to_version, rows=rows
    )


@router.post("/rebaseline", response_model=BaselineOut)
def post_rebaseline(
    project_id: int,
    body: RebaselineRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "schedule.rebaseline")
    ensure_project_write(project_id, user, codes, db)
    _get_project(db, project_id)
    raise HTTPException(
        status_code=400,
        detail="Rebaseline hanya via POST .../rebaseline/request, client ack, lalu Management decide",
    )


@router.get("/rebaseline-history", response_model=list[RebaselineMarker])
def get_rebaseline_history(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    return rebaseline_markers(db, project_id)


@router.post("/progress/weeks/{week_start}/save", response_model=SaveWeekResponse)
def save_week_progress(
    project_id: int,
    week_start: date,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "schedule.save_week")
    ensure_project_write(project_id, user, codes, db)
    project = _get_project(db, project_id)
    week_key, _ = snapshot_anchor_for_date(project, week_start)
    existing = db.scalar(
        select(ProgressSnapshot).where(
            ProgressSnapshot.project_id == project_id,
            ProgressSnapshot.week_start == week_key,
        )
    )

    snap = save_weekly_progress(
        db,
        project_id,
        week_start,
        ProgressSnapshotSource.manual_save,
    )
    log_activity(
        db,
        project_id,
        user.id,
        "progress.week.saved",
        {"week_start": week_key.isoformat(), "created": existing is None},
    )
    db.commit()
    db.refresh(snap)
    return SaveWeekResponse(created=existing is None, snapshot=_snapshot_out(snap))


@router.get("/progress/snapshots", response_model=list[ProgressSnapshotOut])
def list_progress_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    snaps = db.scalars(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.week_start)
    ).all()
    return [_snapshot_out(s) for s in snaps]


@router.get("/report-anchors")
def list_report_anchors(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    from datetime import date

    from app.services.report_calendar import (
        calendar_anchor_window,
        filter_anchors_through_active_week,
        first_schedule_anchor_date,
        period_for_anchor,
        resolve_report_period,
    )
    from app.services.schedule_window import project_report_end_date
    from app.services.weekly_report_anchors import resolve_project_anchor_context

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")

    today = date.today()
    schedule_warning: str | None = None
    try:
        project_end, _ = project_report_end_date(db, project_id)
        cap = project_end or today
        anchors, range_start, project_start, project_end, bound_source = (
            resolve_project_anchor_context(db, project, cap_at=cap)
        )
    except ValueError as exc:
        err = str(exc)
        first = project.weekly_report_first_anchor_date
        if first is not None and "Tanggal laporan pertama" in err:
            schedule_warning = err
            project.weekly_report_first_anchor_date = None
            try:
                anchors, range_start, project_start, project_end, bound_source = (
                    resolve_project_anchor_context(db, project, cap_at=cap)
                )
            finally:
                project.weekly_report_first_anchor_date = first
        else:
            raise HTTPException(status_code=400, detail=err) from exc
    anchors_started = filter_anchors_through_active_week(
        anchors, project.weekly_report_anchor_weekday, today
    )
    min_first = (
        first_schedule_anchor_date(project_start, project.weekly_report_anchor_weekday).isoformat()
        if project_start
        else None
    )
    if not anchors:
        bound_source = "calendar_fallback"
        anchors = calendar_anchor_window(today, project.weekly_report_anchor_weekday)
        project_end = None
        project_start = None
        range_start = None
    pstart = project_start or project_report_start_date(db, project_id)

    def _anchor_row(rd: date) -> dict:
        ps, report_date = period_for_anchor(
            rd,
            project.weekly_report_cutoff_offset_days,
            pstart,
            report_weekday=project.weekly_report_anchor_weekday,
            explicit_first_report_date=project.weekly_report_first_anchor_date,
        )
        return {
            "report_date": report_date.isoformat(),
            "period_start": ps.isoformat(),
            "anchor_date": report_date.isoformat(),
            "cut_off_date": report_date.isoformat(),
        }

    out = [_anchor_row(a) for a in anchors]
    _, _, next_rd = resolve_report_period(
        project.weekly_report_anchor_weekday,
        project.weekly_report_cutoff_offset_days,
        None,
        today,
        project_start_date=pstart,
        explicit_first_report_date=project.weekly_report_first_anchor_date,
    )
    out_started = [_anchor_row(a) for a in anchors_started]
    from app.services.progress_metrics import active_report_week_context

    active_report_date, active_period_start, active_status, status_date_report = (
        active_report_week_context(project, db=db)
    )
    return {
        "anchors": out,
        "anchors_started": out_started,
        "report_weekday": project.weekly_report_anchor_weekday,
        "period_length_days": project.weekly_report_cutoff_offset_days,
        "active_report_date": active_report_date.isoformat(),
        "active_period_start": active_period_start.isoformat(),
        "active_cut_off_date": active_status.isoformat(),
        "status_date_report": status_date_report.isoformat(),
        "next_report_date_hint": next_rd.isoformat(),
        "active_anchor_date": active_report_date.isoformat(),
        "next_anchor_hint": next_rd.isoformat(),
        "project_start_date": project_start.isoformat() if project_start else None,
        "schedule_end": project_end.isoformat() if project_end else None,
        "range_start": range_start.isoformat() if range_start else None,
        "bounds_source": bound_source,
        "min_first_report_date": min_first,
        "min_first_anchor_date": min_first,
        "first_report_date": project.weekly_report_first_anchor_date.isoformat()
        if project.weekly_report_first_anchor_date
        else None,
        "weekly_report_first_anchor_date": project.weekly_report_first_anchor_date.isoformat()
        if project.weekly_report_first_anchor_date
        else None,
        "schedule_warning": schedule_warning,
    }


@router.post("/report-anchors/generate")
def generate_report_anchor_targets(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    """Target anchor + planned progress kumulatif per minggu s.d. akhir proyek."""
    ensure_permission(codes, "schedule.save_week")
    ensure_project_write(project_id, user, codes, db)
    _get_project(db, project_id)
    try:
        result = seed_planned_weekly_targets(db, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return result


@router.get("/scurve/export")
def export_scurve_xlsx(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    from pathlib import Path

    from fastapi.responses import FileResponse

    from app.config import settings
    from app.models import Project
    from app.services.schedule_window import project_report_end_date, scurve_allowed
    from app.services.scurve_excel import export_scurve_workbook
    from app.services.weekly_report_anchors import anchor_target_series

    _auth_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    from app.services.schedule_window import kickoff_milestones, reports_scurve_available

    if not project or not reports_scurve_available(db, project):
        raise HTTPException(
            status_code=400,
            detail="S-curve membutuhkan timeline kick off atau generate target weekly report.",
        )
    project_end, _ = project_report_end_date(db, project_id)
    if not project_end:
        raise HTTPException(status_code=400, detail="Timeline kick off belum ada")
    ms = kickoff_milestones(db, project_id)
    try:
        _, series, _ = anchor_target_series(db, project, cap_at=project_end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    series = [
        {
            "date": r["anchor_date"],
            "cut_off": r["cut_off_date"],
            "planned_pct": r["planned_cumulative_pct"],
        }
        for r in series
    ]
    out_dir = Path(settings.upload_dir) / "scurve" / str(project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{project.code}_scurve.xlsx"
    export_scurve_workbook(
        dest,
        project_code=project.code,
        project_name=project.name,
        series=series,
        db=db,
        project=project,
        milestones=ms,
    )
    return FileResponse(dest, filename=dest.name)


class MilestoneChartItem(BaseModel):
    id: int
    name: str
    weight_pct: float
    planned_pct: float
    actual_pct: float


class MilestoneChartOut(BaseModel):
    as_of: str | None
    cut_off_date: str | None
    status_date_report: str | None = None
    active_anchor_date: str | None = None
    active_period_start: str | None = None
    items: list[MilestoneChartItem]


@router.get("/milestone-chart", response_model=MilestoneChartOut)
def get_milestone_chart(
    project_id: int,
    as_of: date | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    return milestone_chart_points(db, project_id, as_of=as_of)


@router.get("/scurve", response_model=list[ScurvePoint])
def get_scurve(
    project_id: int,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _auth_read(project_id, user, codes, db)
    from app.services.schedule_window import project_report_end_date, project_report_start_date

    project = db.get(Project, project_id)
    project_end, _ = project_report_end_date(db, project_id)
    sch_start = project_report_start_date(db, project) if project else None
    return scurve_points(
        db,
        project_id,
        date_from=date_from or sch_start,
        date_to=date_to,
    )
