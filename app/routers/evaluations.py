from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read
from app.database import get_db
from app.models import ProjectEvaluation, User
from app.services.evaluation import compute_evaluation

router = APIRouter(prefix="/projects/{project_id}/evaluations", tags=["evaluations"])


@router.get("")
def list_evaluations(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "evaluations.read", "dashboard.executive")
    ensure_project_read(project_id, user, codes, db)
    rows = db.scalars(
        select(ProjectEvaluation)
        .where(ProjectEvaluation.project_id == project_id)
        .order_by(ProjectEvaluation.computed_at.desc())
        .limit(20)
    ).all()
    return [
        {
            "id": r.id,
            "sph_planned_md": r.sph_planned_md,
            "actual_md": r.actual_md,
            "variance_md": r.variance_md,
            "variance_cost": r.variance_cost,
            "computed_at": r.computed_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/compute")
def run_compute(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "evaluations.read", "projects.write")
    ensure_project_read(project_id, user, codes, db)
    ev = compute_evaluation(db, project_id)
    db.commit()
    return {
        "sph_planned_md": ev.sph_planned_md,
        "actual_md": ev.actual_md,
        "variance_md": ev.variance_md,
        "variance_cost": ev.variance_cost,
    }
