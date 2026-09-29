from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ProjectHealthConfig, ProjectPhase, ProjectStatus
from app.services.progress_metrics import delivery_week_metrics, snapshot_for_anchor_week
from app.services.schedule import compute_spi, get_current_baseline
from app.services.schedule_window import project_health_metrics_enabled


def rag_from_spi(spi: float, cfg: ProjectHealthConfig) -> str:
    if spi >= cfg.spi_green_min:
        return "green"
    if spi >= cfg.spi_yellow_min:
        return "yellow"
    return "red"


def rag_from_gap(gap: float, cfg: ProjectHealthConfig) -> str:
    if gap <= cfg.progress_gap_green_max:
        return "green"
    if gap <= cfg.progress_gap_yellow_max:
        return "yellow"
    return "red"


def worst_rag(a: str, b: str) -> str:
    order = {"green": 0, "yellow": 1, "red": 2}
    return a if order[a] >= order[b] else b


def _project_schedule_dates(db: Session, project: Project) -> tuple[str | None, str | None]:
    from app.models import ProjectPo
    from app.services.schedule_window import timeline_report_bounds

    start = project.planned_start_date
    if not start:
        start, _, _ = timeline_report_bounds(db, project.id)
    end = project.planned_end_date
    if not end:
        _, end, _ = timeline_report_bounds(db, project.id)
    if not end:
        end = project.po_due_date
    po = db.get(ProjectPo, project.id)
    if po and po.po_due_date:
        end = po.po_due_date
    return (
        start.isoformat() if start else None,
        end.isoformat() if end else None,
    )


def compute_health(db: Session, project_id: int, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    project = db.get(Project, project_id)
    cfg = db.get(ProjectHealthConfig, project_id)
    if not cfg:
        cfg = ProjectHealthConfig(project_id=project_id)
        db.add(cfg)
        db.flush()

    start_iso, end_iso = _project_schedule_dates(db, project) if project else (None, None)

    if not project or not project_health_metrics_enabled(project):
        return {
            "project_id": project_id,
            "as_of": as_of.isoformat(),
            "planned_progress_pct": None,
            "actual_progress_pct": None,
            "spi": None,
            "progress_gap": None,
            "progress_deviation_pct": None,
            "status_date": as_of.isoformat(),
            "rag_schedule": None,
            "rag_gap": None,
            "rag_overall": None,
            "baseline_version": None,
            "health_source": "pre_delivery",
            "project_start_date": start_iso,
            "project_end_date": end_iso,
            "config": {
                "spi_green_min": cfg.spi_green_min,
                "spi_yellow_min": cfg.spi_yellow_min,
                "progress_gap_green_max": cfg.progress_gap_green_max,
                "progress_gap_yellow_max": cfg.progress_gap_yellow_max,
            },
            "lifecycle_bucket": "running",
        }

    baseline = get_current_baseline(db, project_id)
    planned, actual, status_cutoff, anchor, health_source = delivery_week_metrics(
        db, project, as_of=as_of if as_of != date.today() else None
    )
    snap = snapshot_for_anchor_week(db, project_id, anchor)

    spi = compute_spi(actual, planned)
    deviation = round(actual - planned, 2)
    gap = round(planned - actual, 2)
    rag_spi = rag_from_spi(spi, cfg) if spi is not None else None
    rag_g = rag_from_gap(gap, cfg)
    out = {
        "project_id": project_id,
        "as_of": as_of.isoformat(),
        "status_date": status_cutoff.isoformat(),
        "planned_progress_pct": planned,
        "actual_progress_pct": actual,
        "progress_deviation_pct": deviation,
        "spi": spi,
        "progress_gap": gap,
        "rag_schedule": rag_spi,
        "rag_gap": rag_g,
        "rag_overall": rag_spi,
        "baseline_version": baseline.version if baseline else None,
        "health_source": health_source,
        "config": {
            "spi_green_min": cfg.spi_green_min,
            "spi_yellow_min": cfg.spi_yellow_min,
            "progress_gap_green_max": cfg.progress_gap_green_max,
            "progress_gap_yellow_max": cfg.progress_gap_yellow_max,
        },
        "lifecycle_bucket": (
            "completed"
            if project
            and (
                project.status == ProjectStatus.closed
                or project.current_phase == ProjectPhase.closed
            )
            else "running"
        ),
        "project_start_date": start_iso,
        "project_end_date": end_iso,
    }
    out["report_date"] = anchor.isoformat()
    out["report_anchor_date"] = anchor.isoformat()
    out["report_cut_off_date"] = status_cutoff.isoformat()
    out["active_report_date"] = anchor.isoformat()
    if snap:
        if snap.week_end > snap.week_start:
            # Legacy forward: kolom DB sudah menyimpan rentang tampilan.
            out["snapshot_week_start"] = snap.week_start.isoformat()
            out["snapshot_week_end"] = snap.week_end.isoformat()
        else:
            from app.services.report_calendar import period_for_report_date
            from app.services.schedule_window import project_report_start_date

            report_date = snap.week_end
            pstart = project_report_start_date(db, project)
            period_start, _ = period_for_report_date(
                report_date,
                project.weekly_report_cutoff_offset_days,
                pstart,
            )
            out["snapshot_week_start"] = period_start.isoformat()
            out["snapshot_week_end"] = report_date.isoformat()
    return out
