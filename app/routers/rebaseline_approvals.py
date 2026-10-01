from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ApprovalStatus, Project, ProjectPhase, RebaselineRequest, User
from app.services.health import compute_health
from app.services.rebaseline_diff import (
    ProposedPhaseIn,
    RebaselineCategory,
    apply_proposed_phases_to_live,
    build_proposed_changes_payload,
    load_baseline_phases,
    load_live_phase_seed,
    live_phase_lifecycle_by_id,
    load_rebaseline_phase_guide,
    normalize_proposed_phases,
    recalc_proposed_phases_dates,
)
from app.services.schedule import rebaseline_project

router = APIRouter(prefix="/projects/{project_id}/rebaseline", tags=["rebaseline"])
global_router = APIRouter(prefix="/rebaseline", tags=["rebaseline"])


class ProposedPhaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float = 0.0
    milestone_id: int | None = None
    client_key: str | None = None
    sort_order: int = 0
    notes: str | None = None
    predecessor_ref: str | None = None
    predecessor_link_type: str | None = None
    duration_days: int | None = None


class RebaselineRequestIn(BaseModel):
    reason: str
    category: RebaselineCategory
    effective_from: date
    proposed_phases: list[ProposedPhaseBody]


class RebaselineValidateIn(BaseModel):
    category: RebaselineCategory
    effective_from: date
    proposed_phases: list[ProposedPhaseBody]


class RebaselineRecalcDatesIn(BaseModel):
    effective_from: date
    proposed_phases: list[ProposedPhaseBody]


class ClientAckBody(BaseModel):
    client_acknowledged: bool = True


class DecideBody(BaseModel):
    approve: bool
    comment: str | None = None


def _scope_change_eligible(project: Project) -> bool:
    if project.delivery_started_at:
        return True
    return project.current_phase in (
        ProjectPhase.in_delivery,
        ProjectPhase.bast,
    )


def _delay_eligible(db: Session, project_id: int) -> bool:
    health = compute_health(db, project_id)
    planned = float(health.get("planned_progress_pct") or 0)
    actual = float(health.get("actual_progress_pct") or 0)
    behind = actual < planned - 0.01
    rag = health.get("rag_overall")
    return behind and rag in ("yellow", "red")


def _assert_category_eligible(db: Session, project: Project, category: RebaselineCategory) -> None:
    if category == "delay":
        if not _delay_eligible(db, project.id):
            raise HTTPException(
                status_code=400,
                detail="Kategori keterlambatan memerlukan actual di bawah target dan RAG kuning/merah.",
            )
    elif not _scope_change_eligible(project):
        raise HTTPException(
            status_code=400,
            detail="Perubahan scope hanya tersedia setelah delivery dimulai.",
        )


def _build_payload_from_body(db: Session, project_id: int, body: RebaselineValidateIn) -> dict:
    proposed = normalize_proposed_phases(
        [ProposedPhaseIn.model_validate(p.model_dump()) for p in body.proposed_phases]
    )
    return build_proposed_changes_payload(
        db, project_id, body.category, body.effective_from, proposed
    )


@router.get("/preview")
def rebaseline_preview(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "schedule.read", "rebaseline.request", "rebaseline.approve")
    ensure_project_read(project_id, user, codes, db)
    baseline_version, baseline_phases = load_baseline_phases(db, project_id)
    seed = load_live_phase_seed(db, project_id)
    phase_guide, phase_summary = load_rebaseline_phase_guide(db, project_id)
    guide_by_id = {g["milestone_id"]: g for g in phase_guide}
    seed_enriched: list[dict] = []
    for p in seed:
        row = p.model_dump_jsonable()
        meta = guide_by_id.get(p.milestone_id)
        if meta:
            row["lifecycle"] = meta["lifecycle"]
            row["can_delete"] = meta["can_delete"]
            row["can_edit_weight"] = meta["can_edit_weight"]
            row["clickup_workflow"] = meta["clickup_workflow"]
            row["pdc_status"] = meta["pdc_status"]
        seed_enriched.append(row)
    project = db.get(Project, project_id)
    return {
        "baseline_version": baseline_version,
        "baseline_phases": [p.model_dump_jsonable() for p in baseline_phases],
        "seed_from_live": seed_enriched,
        "phase_guide": phase_guide,
        "phase_summary": phase_summary,
        "eligibility": {
            "delay": _delay_eligible(db, project_id) if project else False,
            "scope_change": _scope_change_eligible(project) if project else False,
        },
        "clickup_note": (
            "Progress actual mengikuti ClickUp. Setelah rebaseline disetujui, jalankan sync ClickUp "
            "dan pastikan fase/task baru terhubung ke list atau task ClickUp agar progress tetap akurat."
        ),
    }


@router.post("/recalc-dates")
def rebaseline_recalc_dates(
    project_id: int,
    body: RebaselineRecalcDatesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "rebaseline.request", "schedule.rebaseline", "schedule.read")
    ensure_project_read(project_id, user, codes, db)
    proposed = normalize_proposed_phases(
        [ProposedPhaseIn.model_validate(p.model_dump()) for p in body.proposed_phases]
    )
    lifecycle = live_phase_lifecycle_by_id(db, project_id)
    recalc = recalc_proposed_phases_dates(
        db,
        proposed,
        project_start=body.effective_from,
        lifecycle_by_id=lifecycle,
    )
    return {
        "proposed_phases": [p.model_dump_jsonable() for p in recalc],
    }


@router.post("/validate")
def rebaseline_validate(
    project_id: int,
    body: RebaselineValidateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "rebaseline.request", "schedule.rebaseline")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_category_eligible(db, project, body.category)
    return _build_payload_from_body(db, project_id, body)


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
            "proposed_changes": r.proposed_changes or {},
        }
        for r in rows
    ]


def _pending_rebaseline_rows(db: Session) -> list[dict]:
    rows = db.execute(
        select(RebaselineRequest, Project)
        .join(Project, Project.id == RebaselineRequest.project_id)
        .where(RebaselineRequest.status == ApprovalStatus.pending)
        .order_by(RebaselineRequest.created_at.asc())
    ).all()
    out = []
    for req, proj in rows:
        out.append(
            {
                "id": req.id,
                "project_id": proj.id,
                "project_code": proj.code,
                "reason": req.reason,
                "status": req.status.value,
                "client_acknowledged": req.client_acknowledged,
                "created_at": req.created_at.isoformat(),
                "proposed_changes": req.proposed_changes or {},
            }
        )
    return out


@global_router.get("/pending")
def list_pending_global(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "rebaseline.approve", "approvals.decide")
    return _pending_rebaseline_rows(db)


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
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_category_eligible(db, project, body.category)
    pending = db.scalar(
        select(RebaselineRequest).where(
            RebaselineRequest.project_id == project_id,
            RebaselineRequest.status == ApprovalStatus.pending,
        )
    )
    if pending:
        raise HTTPException(status_code=400, detail="Rebaseline request already pending")
    validate_body = RebaselineValidateIn(
        category=body.category,
        effective_from=body.effective_from,
        proposed_phases=body.proposed_phases,
    )
    payload = _build_payload_from_body(db, project_id, validate_body)
    errors = payload.get("validation", {}).get("blocking_errors") or []
    if errors:
        raise HTTPException(status_code=400, detail={"blocking_errors": errors, "payload": payload})
    req = RebaselineRequest(
        project_id=project_id,
        reason=body.reason,
        proposed_changes=payload,
        requested_by_id=user.id,
    )
    db.add(req)
    db.commit()
    return {"id": req.id, "status": req.status.value, "proposed_changes": payload}


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
        pc = req.proposed_changes or {}
        proposed = pc.get("proposed_phases") or []
        if not proposed:
            raise HTTPException(status_code=400, detail="Missing proposed_phases in request")
        apply_proposed_phases_to_live(db, project_id, proposed)
        eff = date.today()
        if isinstance(pc.get("effective_from"), str):
            eff = date.fromisoformat(pc["effective_from"][:10])
        baseline = rebaseline_project(db, project_id, eff, req.reason, user.id)
        req.status = ApprovalStatus.approved
        req.resulting_baseline_version = baseline.version
    else:
        req.status = ApprovalStatus.rejected
    db.commit()
    return {"status": req.status.value, "baseline_version": req.resulting_baseline_version}
