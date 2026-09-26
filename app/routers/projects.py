from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import (
    Milestone,
    MilestoneStatus,
    Project,
    ProjectHealthConfig,
    ProjectMember,
    ProjectMemberRole,
    ProjectMethodology,
    ProjectPhase,
    ProjectPhaseRecord,
    ProjectPo,
    ProjectSph,
    ProjectStatus,
    User,
)
from app.services.activity import log_activity
from app.services.authorization import filter_projects_for_user
from app.services.bast import bast_checklist_for_display, default_bast_checklist, normalize_bast_checklist
from app.services.bootstrap import ensure_project_owner_member
from app.services.health import compute_health
from app.services.progress import resolve_actual_progress
from app.services.project_code import next_project_code
from app.services.project_delete import delete_project, validate_project_deletion
from app.services.workflow import (
    apply_phase_transition,
    ensure_phase_row,
    next_phase,
    validate_phase_transition,
)

router = APIRouter(prefix="/projects", tags=["projects"])


class MilestoneIn(BaseModel):
    name: str
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float = 0
    status: str = "open"
    actual_date: date | None = None


class ProjectMetaPatch(BaseModel):
    po_due_date: date | None = None
    document_repo_url: str | None = None
    project_manager: str | None = None
    methodology: str | None = None
    weekly_report_anchor_weekday: int | None = Field(default=None, ge=0, le=6)
    weekly_report_cutoff_offset_days: int | None = None
    weekly_report_first_anchor_date: date | None = None


class ProjectCreate(BaseModel):
    code: str | None = None
    name: str = ""
    client_name: str = ""
    sph_name: str | None = None
    sph_client: str | None = None
    sph_no: str | None = None
    project_manager: str | None = None
    po_date: date | None = None
    contract_value: float | None = Field(default=None, ge=0)


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    client_name: str
    current_phase: str
    delivery_started_at: str | None
    owner_id: int
    po_due_date: str | None = None
    po_sub_total: float | None = None
    contract_value: float | None = None
    sph_total_rupiah: float | None = None
    planned_progress_pct: float | None = None
    actual_progress_pct: float | None = None
    progress_deviation_pct: float | None = None
    spi: float | None = None
    rag_overall: str | None = None
    status_date: str | None = None
    sph_no: str | None = None
    project_manager: str | None = None

    model_config = {"from_attributes": True}


@router.get("/next-code")
def preview_next_code(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("projects.write")),
):
    return {"code": next_project_code(db)}


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.read.own", "projects.read.all")
    projects = filter_projects_for_user(db, user, codes)
    out: list[ProjectOut] = []
    for p in projects:
        po = db.get(ProjectPo, p.id)
        sph = db.get(ProjectSph, p.id)
        due = p.po_due_date
        if po and po.po_due_date:
            due = po.po_due_date
        health = compute_health(db, p.id)
        out.append(
            ProjectOut(
                id=p.id,
                code=p.code,
                name=p.name,
                client_name=p.client_name,
                current_phase=p.current_phase.value,
                delivery_started_at=p.delivery_started_at.isoformat()
                if p.delivery_started_at
                else None,
                owner_id=p.owner_id,
                po_due_date=due.isoformat() if due else None,
                po_sub_total=po.po_sub_total if po else None,
                contract_value=p.contract_value,
                sph_total_rupiah=sph.sph_total_rupiah if sph else None,
                planned_progress_pct=health.get("planned_progress_pct"),
                actual_progress_pct=health.get("actual_progress_pct"),
                progress_deviation_pct=health.get("progress_deviation_pct"),
                spi=health.get("spi"),
                rag_overall=health.get("rag_overall"),
                status_date=health.get("status_date"),
                sph_no=sph.sph_no if sph else None,
                project_manager=p.project_manager,
            )
        )
    return out


@router.post("", response_model=ProjectOut)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("projects.write")),
):
    display_name = (body.sph_name or body.name or "").strip() or "Proyek baru"
    client = (body.sph_client or body.client_name or "").strip()
    code = (body.code or "").strip() or next_project_code(db)
    if db.query(Project).filter(Project.code == code).first():
        raise HTTPException(status_code=400, detail="Project code exists")
    pm = (body.project_manager or "").strip() or None
    p = Project(
        code=code,
        name=display_name,
        client_name=client,
        project_manager=pm,
        po_date=body.po_date,
        contract_value=body.contract_value,
        owner_id=user.id,
        bast_checklist=default_bast_checklist(),
    )
    db.add(p)
    db.flush()
    db.add(ProjectHealthConfig(project_id=p.id))
    db.add(
        ProjectMember(
            project_id=p.id,
            user_id=user.id,
            member_role=ProjectMemberRole.owner,
            assigned_by_id=user.id,
        )
    )
    ensure_phase_row(db, p.id, ProjectPhase.po_received)
    db.add(
        ProjectSph(
            project_id=p.id,
            sph_name=display_name,
            sph_client=client or None,
            sph_no=(body.sph_no or "").strip() or None,
            pic_user_name=pm,
        )
    )
    db.commit()
    db.refresh(p)
    return ProjectOut(
        id=p.id,
        code=p.code,
        name=p.name,
        client_name=p.client_name,
        current_phase=p.current_phase.value,
        delivery_started_at=None,
        owner_id=p.owner_id,
        sph_no=(body.sph_no or "").strip() or None,
        project_manager=pm,
    )


@router.post("/{project_id}/milestones/batch")
def add_milestones(
    project_id: int,
    items: list[MilestoneIn],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "milestones.write")
    ensure_project_write(
        project_id,
        user,
        codes,
        db,
        {ProjectMemberRole.owner, ProjectMemberRole.pm, ProjectMemberRole.delivery},
    )
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    for item in items:
        db.add(
            Milestone(
                project_id=project_id,
                name=item.name,
                start_date=item.start_date,
                target_date=item.target_date,
                weight_pct=item.weight_pct,
                status=MilestoneStatus(item.status),
                actual_date=item.actual_date,
            )
        )
    db.commit()
    return {"added": len(items)}


@router.patch("/{project_id}")
def patch_project_meta(
    project_id: int,
    body: ProjectMetaPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.po_due_date is not None:
        p.po_due_date = body.po_due_date
    if body.document_repo_url is not None:
        p.document_repo_url = body.document_repo_url
    if body.project_manager is not None:
        p.project_manager = body.project_manager.strip() or None
        sph = db.get(ProjectSph, project_id)
        if sph:
            sph.pic_user_name = p.project_manager
    if body.methodology is not None:
        try:
            p.methodology = ProjectMethodology(body.methodology)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid methodology") from exc
    if body.weekly_report_anchor_weekday is not None:
        p.weekly_report_anchor_weekday = body.weekly_report_anchor_weekday
    if body.weekly_report_cutoff_offset_days is not None:
        p.weekly_report_cutoff_offset_days = body.weekly_report_cutoff_offset_days
    meta = body.model_dump(exclude_unset=True)
    if "weekly_report_first_anchor_date" in meta:
        from app.services.report_calendar import validate_weekly_first_anchor_date
        from app.services.schedule_window import project_report_start_date

        first = meta["weekly_report_first_anchor_date"]
        if first is not None:
            project_start = project_report_start_date(db, p)
            wd = body.weekly_report_anchor_weekday
            if wd is None:
                wd = p.weekly_report_anchor_weekday
            validate_weekly_first_anchor_date(first, project_start, wd)
        p.weekly_report_first_anchor_date = first
    db.commit()
    return {"updated": True}


@router.get("/{project_id}")
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    health = compute_health(db, project_id)
    sph = db.get(ProjectSph, project_id)
    phases = db.scalars(
        select(ProjectPhaseRecord).where(ProjectPhaseRecord.project_id == project_id)
    ).all()
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "client_name": p.client_name,
        "project_manager": p.project_manager,
        "sph_no": sph.sph_no if sph else None,
        "po_date": p.po_date.isoformat() if p.po_date else None,
        "po_due_date": p.po_due_date.isoformat() if p.po_due_date else None,
        "document_repo_url": p.document_repo_url,
        "contract_value": p.contract_value,
        "clickup_enabled": p.clickup_enabled,
        "current_phase": p.current_phase.value,
        "methodology": p.methodology.value,
        "status": p.status.value,
        "sph_total_rupiah": sph.sph_total_rupiah if sph else None,
        "planned_md": sph.planned_md if sph else None,
        "delivery_started_at": p.delivery_started_at.isoformat()
        if p.delivery_started_at
        else None,
        "weekly_report_anchor_weekday": p.weekly_report_anchor_weekday,
        "weekly_report_cutoff_offset_days": p.weekly_report_cutoff_offset_days,
        "weekly_report_first_anchor_date": p.weekly_report_first_anchor_date.isoformat()
        if p.weekly_report_first_anchor_date
        else None,
        "clickup_provision_status": p.clickup_provision_status,
        "kickoff_timeline_confirmed_at": p.kickoff_timeline_confirmed_at.isoformat()
        if p.kickoff_timeline_confirmed_at
        else None,
        "planned_start_date": p.planned_start_date.isoformat() if p.planned_start_date else None,
        "planned_end_date": p.planned_end_date.isoformat() if p.planned_end_date else None,
        "health": health,
        "bast_checklist": bast_checklist_for_display(p.bast_checklist),
        "phases": [
            {
                "phase": ph.phase.value,
                "started_at": ph.started_at.isoformat() if ph.started_at else None,
                "completed_at": ph.completed_at.isoformat() if ph.completed_at else None,
            }
            for ph in phases
        ],
    }


class BastUpdate(BaseModel):
    bast_checklist: dict
    weekly_notes: str | None = None


@router.patch("/{project_id}/bast")
def patch_bast(
    project_id: int,
    body: BastUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    p.bast_checklist = normalize_bast_checklist(body.bast_checklist)
    if body.weekly_notes is not None:
        p.weekly_notes = body.weekly_notes
    db.commit()
    return {"bast_checklist": p.bast_checklist}


@router.delete("/{project_id}", status_code=204)
def remove_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        delete_project(db, p)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{project_id}/phase-gate")
def get_phase_gate(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    from app.services.phase_gates import phase_gate_status

    return phase_gate_status(db, p)


@router.post("/{project_id}/advance-phase")
def advance_phase(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write", "approvals.request")
    ensure_project_write(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    target = next_phase(p.current_phase)
    if not target:
        raise HTTPException(status_code=400, detail="Tidak ada fase berikutnya")
    try:
        validate_phase_transition(db, p, target)
        apply_phase_transition(db, p, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log_activity(db, project_id, user.id, "phase.advanced", {"to": target.value})
    db.commit()
    return {"current_phase": p.current_phase.value}


@router.post("/{project_id}/close")
def close_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.write")
    ensure_project_write(project_id, user, codes, db)
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        validate_phase_transition(db, p, ProjectPhase.closed)
        apply_phase_transition(db, p, ProjectPhase.closed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    progress = resolve_actual_progress(db, p, datetime.utcnow().date())
    log_activity(
        db,
        project_id,
        user.id,
        "project.closed",
        {"progress": progress},
    )
    db.commit()
    return {"status": p.status.value, "current_phase": p.current_phase.value}
