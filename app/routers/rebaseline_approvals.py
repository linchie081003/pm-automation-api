from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ApprovalStatus, RebaselineRequest, User
from app.services.schedule import rebaseline_project

router = APIRouter(prefix="/projects/{project_id}/rebaseline", tags=["rebaseline"])


class RebaselineRequestIn(BaseModel):
    reason: str
    proposed_changes: dict = {}
    effective_from: date | None = None


class ClientAckBody(BaseModel):
    client_acknowledged: bool = True


class DecideBody(BaseModel):
    approve: bool
    comment: str | None = None


@router.get("/requests")
def list_requests(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "schedule.read", "rebaseline.request", "rebaseline.approve")
    ensure_project_read(project_id, user, codes, db)
    rows = db.scalars(
        select(RebaselineRequest)
        .where(RebaselineRequest.project_id == project_id)
        .order_by(RebaselineRequest.created_at.desc())
    ).all()
    return [
        {
            "id": r.id,
            "reason": r.reason,
            "status": r.status.value,
            "client_acknowledged": r.client_acknowledged,
            "resulting_baseline_version": r.resulting_baseline_version,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/request")
def create_request(
    project_id: int,
    body: RebaselineRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "rebaseline.request", "schedule.rebaseline")
    ensure_project_write(project_id, user, codes, db)
    pending = db.scalar(
        select(RebaselineRequest).where(
            RebaselineRequest.project_id == project_id,
            RebaselineRequest.status == ApprovalStatus.pending,
        )
    )
    if pending:
        raise HTTPException(status_code=400, detail="Rebaseline request already pending")
    req = RebaselineRequest(
        project_id=project_id,
        reason=body.reason,
        proposed_changes=body.proposed_changes,
        requested_by_id=user.id,
    )
    db.add(req)
    db.commit()
    return {"id": req.id, "status": req.status.value}


@router.post("/requests/{request_id}/client-ack")
def client_ack(
    project_id: int,
    request_id: int,
    body: ClientAckBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "rebaseline.request", "schedule.rebaseline")
    ensure_project_write(project_id, user, codes, db)
    req = db.get(RebaselineRequest, request_id)
    if not req or req.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    req.client_acknowledged = body.client_acknowledged
    req.client_ack_at = datetime.utcnow() if body.client_acknowledged else None
    db.commit()
    return {"client_acknowledged": req.client_acknowledged}


@router.post("/requests/{request_id}/decide")
def decide(
    project_id: int,
    request_id: int,
    body: DecideBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("rebaseline.approve", "approvals.decide")),
):
    req = db.get(RebaselineRequest, request_id)
    if not req or req.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if req.status != ApprovalStatus.pending:
        raise HTTPException(status_code=400, detail="Already decided")
    if body.approve and not req.client_acknowledged:
        raise HTTPException(status_code=400, detail="Client acknowledgment required")
    req.decided_by_id = user.id
    req.decided_at = datetime.utcnow()
    req.comment = body.comment
    if body.approve:
        eff = date.today()
        if isinstance(req.proposed_changes.get("effective_from"), str):
            eff = date.fromisoformat(req.proposed_changes["effective_from"][:10])
        baseline = rebaseline_project(db, project_id, eff, req.reason, user.id)
        req.status = ApprovalStatus.approved
        req.resulting_baseline_version = baseline.version
    else:
        req.status = ApprovalStatus.rejected
    db.commit()
    return {"status": req.status.value, "baseline_version": req.resulting_baseline_version}
