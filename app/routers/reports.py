from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import Milestone, Project, User, WeeklyReport
from app.services.activity import log_activity
from app.services.weekly_report import generate_weekly_report, preview_weekly_report
from app.config import settings

router = APIRouter(tags=["reports"])


def _ensure_weekly_reports_read(codes: set[str]) -> None:
    """Lihat daftar / unduh laporan: izin reports atau schedule (S-curve tab Reports)."""
    ensure_permission(
        codes,
        "reports.weekly.download",
        "reports.weekly.generate",
        "schedule.read",
    )


class GenerateWeeklyBody(BaseModel):
    week_start: date | None = None
    anchor_date: date | None = None
    notes: str | None = None
    mitigation_plan: str | None = None
    regenerate: bool = False


@router.get("/projects/{project_id}/weekly-reports")
def list_weekly_reports(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _ensure_weekly_reports_read(codes)
    ensure_project_read(project_id, user, codes, db)
    reports = db.scalars(
        select(WeeklyReport)
        .where(WeeklyReport.project_id == project_id)
        .order_by(WeeklyReport.week_start.desc())
    ).all()
    return [
        {
            "id": r.id,
            "week_start": r.week_start.isoformat(),
            "week_end": r.week_end.isoformat(),
            "period_label": f"{r.week_start.isoformat()} — {r.week_end.isoformat()}",
            "baseline_version": r.baseline_version,
            "generated_at": r.generated_at.isoformat(),
            "frozen_metrics": r.frozen_metrics,
            "has_pptx": bool(r.pptx_path),
        }
        for r in reports
    ]


@router.get("/projects/{project_id}/weekly-reports/preview")
def preview_report(
    project_id: int,
    week_start: date | None = Query(None),
    anchor_date: date | None = Query(None),
    notes: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "reports.weekly.generate")
    ensure_project_read(project_id, user, codes, db)
    ws = anchor_date or week_start or date.today()
    try:
        return preview_weekly_report(db, project_id, ws, notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/projects/{project_id}/weekly-reports/generate")
def generate_report(
    project_id: int,
    body: GenerateWeeklyBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "reports.weekly.generate")
    ensure_project_write(project_id, user, codes, db)
    ws = body.anchor_date or body.week_start or date.today()
    try:
        report = generate_weekly_report(
            db,
            project_id,
            ws,
            user.id,
            body.notes,
            body.mitigation_plan,
            regenerate=body.regenerate,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log_activity(
        db,
        project_id,
        user.id,
        "weekly_report.generated",
        {"report_id": report.id, "week_start": report.week_start.isoformat()},
    )
    db.commit()
    summary = report.summary or {}
    return {
        "id": report.id,
        "week_start": report.week_start.isoformat(),
        "document_version": summary.get("document_version"),
    }


@router.get("/projects/{project_id}/weekly-reports/{report_id}/download")
def download_report(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _ensure_weekly_reports_read(codes)
    ensure_project_read(project_id, user, codes, db)
    report = db.get(WeeklyReport, report_id)
    if not report or report.project_id != project_id or not report.xlsx_path:
        raise HTTPException(status_code=404, detail="Report file not found")
    path = Path(report.xlsx_path)
    if not path.is_file():
        path = Path(settings.upload_dir) / report.xlsx_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, filename=path.name)


@router.get("/projects/{project_id}/weekly-reports/{report_id}/download-pptx")
def download_report_pptx(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    _ensure_weekly_reports_read(codes)
    ensure_project_read(project_id, user, codes, db)
    report = db.get(WeeklyReport, report_id)
    if not report or report.project_id != project_id or not report.pptx_path:
        raise HTTPException(status_code=404, detail="PPTX not found")
    path = Path(report.pptx_path)
    if not path.is_file():
        path = Path(settings.upload_dir) / report.pptx_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, filename=path.name)


@router.get("/reports/task-recap")
def task_recap(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("dashboard.executive", "projects.read.all")),
):
    projects = db.scalars(select(Project)).all()
    rows = []
    for p in projects:
        for m in db.scalars(select(Milestone).where(Milestone.project_id == p.id)):
            rows.append(
                {
                    "source": "milestone",
                    "project_code": p.code,
                    "name": m.name,
                    "status": m.status.value,
                    "target_date": m.target_date.isoformat() if m.target_date else None,
                    "weight_pct": m.weight_pct,
                }
            )
    return rows
