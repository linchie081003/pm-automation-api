from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ChangeRequestStatus, Project, ProjectChangeRequest, User

router = APIRouter(prefix="/projects/{project_id}/change-requests", tags=["change-requests"])


class ChangeRequestIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    background: str | None = None
    scope_change: str | None = None
    schedule_impact_days: int | None = None
    cost_impact_rupiah: float | None = None
    priority: str = "medium"


class ChangeRequestPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    background: str | None = None
    scope_change: str | None = None
    schedule_impact_days: int | None = None
    cost_impact_rupiah: float | None = None
    priority: str | None = None


class DecideBody(BaseModel):
    approve: bool
    comment: str | None = None


def _next_cr_no(db: Session, project: Project) -> str:
    count = db.scalar(
        select(func.count())
        .select_from(ProjectChangeRequest)
        .where(ProjectChangeRequest.project_id == project.id)
    )
    seq = int(count or 0) + 1
    return f"CR-{project.code}-{seq:03d}"


def _out(row: ProjectChangeRequest) -> dict:
    return {
        "id": row.id,
        "cr_no": row.cr_no,
        "title": row.title,
        "background": row.background,
        "scope_change": row.scope_change,
        "schedule_impact_days": row.schedule_impact_days,
        "cost_impact_rupiah": row.cost_impact_rupiah,
        "priority": row.priority,
        "status": row.status.value,
        "requested_by_id": row.requested_by_id,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_comment": row.decision_comment,
        "implemented_at": row.implemented_at.isoformat() if row.implemented_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("")
def list_change_requests(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.read", "projects.write")
    ensure_project_read(project_id, user, codes, db)
    rows = db.scalars(
        select(ProjectChangeRequest)
        .where(ProjectChangeRequest.project_id == project_id)
        .order_by(ProjectChangeRequest.created_at.desc())
    ).all()
    return [_out(r) for r in rows]


@router.post("")
def create_change_request(
    project_id: int,
    body: ChangeRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    row = ProjectChangeRequest(
        project_id=project_id,
        cr_no=_next_cr_no(db, project),
        title=body.title.strip(),
        background=body.background,
        scope_change=body.scope_change,
        schedule_impact_days=body.schedule_impact_days,
        cost_impact_rupiah=body.cost_impact_rupiah,
        priority=(body.priority or "medium").lower()[:16],
        status=ChangeRequestStatus.draft,
        requested_by_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/{cr_id}")
def update_change_request(
    project_id: int,
    cr_id: int,
    body: ChangeRequestPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    row = db.get(ProjectChangeRequest, cr_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if row.status != ChangeRequestStatus.draft:
        raise HTTPException(status_code=400, detail="Hanya CR draft yang dapat diedit")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _out(row)


@router.post("/{cr_id}/submit")
def submit_change_request(
    project_id: int,
    cr_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    row = db.get(ProjectChangeRequest, cr_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if row.status != ChangeRequestStatus.draft:
        raise HTTPException(status_code=400, detail="CR sudah disubmit")
    row.status = ChangeRequestStatus.submitted
    row.submitted_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    db.commit()
    return _out(row)


@router.post("/{cr_id}/decide")
def decide_change_request(
    project_id: int,
    cr_id: int,
    body: DecideBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    row = db.get(ProjectChangeRequest, cr_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if row.status != ChangeRequestStatus.submitted:
        raise HTTPException(status_code=400, detail="CR harus status submitted")
    row.status = (
        ChangeRequestStatus.approved if body.approve else ChangeRequestStatus.rejected
    )
    row.decided_by_id = user.id
    row.decided_at = datetime.utcnow()
    row.decision_comment = body.comment
    row.updated_at = datetime.utcnow()
    db.commit()
    return _out(row)


@router.post("/{cr_id}/implement")
def implement_change_request(
    project_id: int,
    cr_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    row = db.get(ProjectChangeRequest, cr_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if row.status != ChangeRequestStatus.approved:
        raise HTTPException(status_code=400, detail="CR harus approved sebelum implement")
    row.status = ChangeRequestStatus.implemented
    row.implemented_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    db.commit()
    return _out(row)
