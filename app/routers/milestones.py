from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import (
    ClickUpTaskCache,
    Milestone,
    MilestoneStatus,
    Project,
    ProjectMemberRole,
    ScheduleBaselineMilestone,
    TimelineItemType,
    User,
)
from app.services.activity import log_activity
from app.services.progress import (
    build_clickup_lookups,
    clickup_status_mapping_context,
    enrich_milestone_clickup_fields,
)
from app.services.timeline_display import merge_timeline_with_clickup
from app.services.project_lifecycle import (
    kickoff_milestones_editable,
    milestone_progress_editable,
    timeline_project_start_editable,
)
from app.services.live_timeline import apply_project_timeline_start

router = APIRouter(tags=["milestones"])


def _require_milestone_editable(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not kickoff_milestones_editable(project):
        raise HTTPException(
            status_code=400,
            detail="Timeline read-only setelah delivery dimulai",
        )
    return project


class MilestoneOut(BaseModel):
    id: int
    name: str
    module: str | None = None
    start_date: date | None
    target_date: date | None
    weight_pct: float
    status: str
    actual_date: date | None
    is_payment_milestone: bool = False
    duration_days: int | None = None
    item_type: str = "milestone"
    parent_id: int | None = None
    sort_order: int = 0
    clickup_task_id: str | None = None
    clickup_name: str | None = None
    clickup_status: str | None = None
    clickup_status_raw: str | None = None
    clickup_url: str | None = None
    clickup_due_date: str | None = None
    clickup_progress_pct: float | None = None
    display_start: str | None = None
    display_end: str | None = None
    display_duration_days: int | None = None
    clickup_only: bool = False
    phase_id: int | None = None
    phase_name: str | None = None
    parent_clickup_task_id: str | None = None
    depth: int = 0
    expandable: bool = False
    timeline_seq: int | None = None
    phase_status: str | None = None
    timeline_dates_inherited: bool | None = None

    model_config = {"from_attributes": True}


class MilestoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float = 0
    status: str = "open"
    duration_days: int | None = None
    item_type: str = "milestone"
    parent_id: int | None = None
    sort_order: int = 0


class MilestonePatch(BaseModel):
    name: str | None = None
    module: str | None = None
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float | None = None
    status: str | None = None
    actual_date: date | None = None
    is_payment_milestone: bool | None = None
    duration_days: int | None = None
    item_type: str | None = None
    parent_id: int | None = None
    sort_order: int | None = None


@router.get("/projects/{project_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "milestones.read")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    ms = db.scalars(
        select(Milestone)
        .where(Milestone.project_id == project_id)
        .order_by(Milestone.sort_order, Milestone.parent_id.nulls_first(), Milestone.id)
    ).all()
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    ms_list = list(ms)
    with clickup_status_mapping_context(db):
        tasks_by_id, milestone_cache = build_clickup_lookups(ms_list, caches)
        pdc_rows: list[dict] = []
        for m in ms_list:
            base = MilestoneOut.model_validate(m).model_dump()
            base.update(
                enrich_milestone_clickup_fields(
                    m, ms_list, tasks_by_id, milestone_cache, caches=caches, db=db
                )
            )
            pdc_rows.append(base)
        merged = merge_timeline_with_clickup(
            pdc_rows, ms_list, caches, db=db, project=project
        )
    return [MilestoneOut(**row) for row in merged]


@router.post("/projects/{project_id}/milestones", response_model=MilestoneOut)
def create_milestone(
    project_id: int,
    body: MilestoneCreate,
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
    _require_milestone_editable(db, project_id)
    try:
        it = TimelineItemType(body.item_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid item_type") from exc
    m = Milestone(
        project_id=project_id,
        name=body.name.strip(),
        start_date=body.start_date,
        target_date=body.target_date,
        weight_pct=body.weight_pct,
        duration_days=body.duration_days,
        item_type=it,
        parent_id=body.parent_id,
        sort_order=body.sort_order,
        status=MilestoneStatus(body.status),
    )
    db.add(m)
    log_activity(
        db,
        project_id,
        user.id,
        "milestone.created",
        {"name": m.name, "item_type": m.item_type.value},
    )
    db.commit()
    db.refresh(m)
    return MilestoneOut.model_validate(m)


@router.patch("/projects/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut)
def patch_milestone(
    project_id: int,
    milestone_id: int,
    body: MilestonePatch,
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
    if not milestone_progress_editable(project):
        raise HTTPException(
            status_code=400,
            detail="Timeline read-only — fase proyek tidak mengizinkan update",
        )
    m = db.get(Milestone, milestone_id)
    if not m or m.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if m.status == MilestoneStatus.done and "weight_pct" in data:
        if data["weight_pct"] is not None and float(data["weight_pct"]) != float(m.weight_pct):
            raise HTTPException(
                status_code=400,
                detail="Bobot fase selesai terkunci (EVM) — tidak boleh diubah.",
            )
    if not kickoff_milestones_editable(project):
        allowed = {"status", "actual_date"}
        if set(data.keys()) - allowed:
            raise HTTPException(
                status_code=400,
                detail="Baseline timeline locked — hanya status/actual yang bisa diubah. "
                "Perubahan rencana via Change Request.",
            )
    if "status" in data:
        data["status"] = MilestoneStatus(data["status"])
    if "item_type" in data:
        data["item_type"] = TimelineItemType(data["item_type"])
    for k, v in data.items():
        setattr(m, k, v)
    log_activity(
        db,
        project_id,
        user.id,
        "milestone.updated",
        {"milestone_id": m.id, "name": m.name, "fields": list(data.keys())},
    )
    db.commit()
    db.refresh(m)
    return MilestoneOut.model_validate(m)


class TimelineProjectStartBody(BaseModel):
    start_date: date


@router.post("/projects/{project_id}/timeline/project-start")
def set_timeline_project_start(
    project_id: int,
    body: TimelineProjectStartBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    """Geser jadwal Milestone delivery dari tanggal start; draft SPH/Kick Off tidak berubah."""
    ensure_permission(codes, "milestones.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not timeline_project_start_editable(project):
        raise HTTPException(
            status_code=400,
            detail="Tanggal start proyek hanya dapat diubah setelah timeline Kick Off dikonfirmasi.",
        )
    try:
        result = apply_project_timeline_start(db, project, body.start_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_activity(
        db,
        project_id,
        user.id,
        "timeline.project_start",
        {"start_date": body.start_date.isoformat()},
    )
    db.commit()
    return result


@router.delete("/projects/{project_id}/milestones/{milestone_id}", status_code=204)
def delete_milestone(
    project_id: int,
    milestone_id: int,
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
    _require_milestone_editable(db, project_id)
    m = db.get(Milestone, milestone_id)
    if not m or m.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    db.execute(
        delete(ScheduleBaselineMilestone).where(
            ScheduleBaselineMilestone.milestone_id == milestone_id
        )
    )
    log_activity(
        db,
        project_id,
        user.id,
        "milestone.deleted",
        {"milestone_id": milestone_id, "name": m.name},
    )
    db.delete(m)
    db.commit()
