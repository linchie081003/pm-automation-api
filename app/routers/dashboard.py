from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.database import get_db
from app.models import Project, ProjectPhase, ProjectSph, ProjectStatus, User
from app.services.authorization import filter_projects_for_user
from app.services.health import compute_health
from app.services.project_phases import visible_on_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def operational_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.read.own", "projects.read.all")),
):
    projects = filter_projects_for_user(db, user, codes)
    active = [p for p in projects if visible_on_dashboard(p)]
    by_phase: dict[str, int] = {}
    for p in active:
        key = p.current_phase.value
        by_phase[key] = by_phase.get(key, 0) + 1
    return {"total": len(active), "by_phase": by_phase}


@router.get("/executive")
def executive_dashboard(
    bucket: str = Query("running", pattern="^(running|completed)$"),
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("dashboard.executive")),
):
    projects = db.query(Project).all()
    rows = []
    for p in projects:
        health = compute_health(db, p.id)
        is_completed = (
            p.status == ProjectStatus.closed or p.current_phase == ProjectPhase.closed
        )
        if bucket == "running":
            if is_completed:
                continue
            if p.current_phase == ProjectPhase.po_received:
                continue
        if bucket == "completed" and not is_completed:
            continue
        sph = db.get(ProjectSph, p.id)
        rows.append(
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "client_name": p.client_name,
                "current_phase": p.current_phase.value,
                "planned_progress_pct": health["planned_progress_pct"],
                "actual_progress_pct": health["actual_progress_pct"],
                "progress_deviation_pct": health.get("progress_deviation_pct"),
                "status_date": health.get("status_date"),
                "spi": health["spi"],
                "rag_overall": health["rag_overall"],
                "sph_total_rupiah": sph.sph_total_rupiah if sph else None,
                "planned_md": sph.planned_md if sph else None,
                "sph_no": sph.sph_no if sph else None,
                "project_manager": p.project_manager,
            }
        )
    rag_dist = {"green": 0, "yellow": 0, "red": 0}
    spi_sum = 0.0
    spi_count = 0
    for r in rows:
        rag = r.get("rag_overall")
        if rag in rag_dist:
            rag_dist[rag] += 1
        spi = r.get("spi")
        if spi is not None:
            spi_sum += float(spi)
            spi_count += 1
    avg_spi = round(spi_sum / spi_count, 4) if spi_count else None
    return {
        "bucket": bucket,
        "count": len(rows),
        "avg_spi": avg_spi,
        "rag_distribution": rag_dist,
        "projects": rows,
    }
