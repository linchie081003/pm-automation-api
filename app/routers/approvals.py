from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_project_read
from app.database import get_db
from app.models import ApprovalRequest, ApprovalStatus, Project, User
from app.services.workflow import decide_approval, request_phase_transition

router = APIRouter(tags=["approvals"])


class DecideBody(BaseModel):
    approve: bool
    comment: str | None = None


class ApprovalOut(BaseModel):
    id: int
    project_id: int
    project_code: str
    from_phase: str
    to_phase: str
    status: str
    requested_by_id: int
    comment: str | None

    model_config = {"from_attributes": True}


@router.get("/approvals/pending", response_model=list[ApprovalOut])
def list_pending(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("approvals.decide")),
):
    rows = db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.pending)
    ).all()
    out = []
    for r in rows:
        p = db.get(Project, r.project_id)
        out.append(
            ApprovalOut(
                id=r.id,
                project_id=r.project_id,
                project_code=p.code if p else "",
                from_phase=r.from_phase.value,
                to_phase=r.to_phase.value,
                status=r.status.value,
                requested_by_id=r.requested_by_id,
                comment=r.comment,
            )
        )
    return out


@router.post("/projects/{project_id}/phase-transition-request")
def request_transition(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("approvals.request")),
):
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        req = request_phase_transition(db, project, user.id)
        db.commit()
        return {"approval_id": req.id, "to_phase": req.to_phase.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/approvals/{approval_id}/decide")
def decide(
    approval_id: int,
    body: DecideBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("approvals.decide")),
):
    approval = db.get(ApprovalRequest, approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        decide_approval(db, approval, body.approve, user.id, body.comment)
        db.commit()
        return {"status": approval.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
