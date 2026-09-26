from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ProjectHealthConfig, User
from app.services.health import compute_health

router = APIRouter(tags=["health"])


class HealthConfigPatch(BaseModel):
    spi_green_min: float | None = None
    spi_yellow_min: float | None = None
    progress_gap_green_max: float | None = None
    progress_gap_yellow_max: float | None = None
    rag_notes: str | None = None


@router.get("/projects/{project_id}/health")
def get_project_health(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    return compute_health(db, project_id)


@router.get("/projects/{project_id}/health/config")
def get_health_config(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    cfg = db.get(ProjectHealthConfig, project_id)
    if not cfg:
        cfg = ProjectHealthConfig(project_id=project_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return {
        "spi_green_min": cfg.spi_green_min,
        "spi_yellow_min": cfg.spi_yellow_min,
        "progress_gap_green_max": cfg.progress_gap_green_max,
        "progress_gap_yellow_max": cfg.progress_gap_yellow_max,
        "rag_notes": cfg.rag_notes,
    }


@router.patch("/projects/{project_id}/health/config")
def patch_health_config(
    project_id: int,
    body: HealthConfigPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("health.config.write")),
):
    ensure_project_write(project_id, user, codes, db)
    cfg = db.get(ProjectHealthConfig, project_id)
    if not cfg:
        cfg = ProjectHealthConfig(project_id=project_id)
        db.add(cfg)
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(cfg, field, val)
    db.commit()
    return {"updated": True}
