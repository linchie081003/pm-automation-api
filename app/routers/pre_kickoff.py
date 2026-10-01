from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import PreKickoffPack, Project, ProjectSph, User
from app.services.deck_kickoff import build_kickoff_deck_path, generate_kickoff_deck
from app.services.deck_pre_kickoff import build_pre_kickoff_deck_path, generate_pre_kickoff_deck
from app.services.draft_timeline import draft_project_timeline_summary
from app.services.kickoff_timeline import confirm_kickoff_timeline, list_draft_timeline
from app.services.pre_kickoff import check_pack_complete, sync_pack_from_sph
from app.services.activity import log_activity
from app.services.project_lifecycle import prior_phase_data_locked

router = APIRouter(prefix="/projects/{project_id}/pre-kickoff", tags=["pre-kickoff"])


class LineItemIn(BaseModel):
    id: str | None = None
    text: str = ""


class PreKickoffUpdate(BaseModel):
    background: str | None = None
    scope: str | None = None
    non_scope: str | None = None
    timeline_summary: str | None = None
    org_structure: str | None = None
    org_vendor: str | None = None
    org_client: str | None = None
    deliverables: str | None = None
    deliverables_items: list[LineItemIn] | None = None
    next_activities: str | None = None


def _pack_out(db: Session, pack: PreKickoffPack) -> dict:
    project = db.get(Project, pack.project_id)
    sph = db.get(ProjectSph, pack.project_id)
    draft_ready = bool(sph and sph.draft_baseline_generated_at)
    return {
        "project_id": pack.project_id,
        "background": pack.background,
        "scope": pack.scope,
        "non_scope": pack.non_scope,
        "timeline_summary": pack.timeline_summary,
        "org_structure": pack.org_structure,
        "org_vendor": pack.org_vendor,
        "org_client": pack.org_client,
        "deliverables": pack.deliverables,
        "deliverables_items": pack.deliverables_items or [],
        "next_activities": pack.next_activities,
        "is_complete": check_pack_complete(pack),
        "deck_generated_at": pack.deck_generated_at.isoformat() if pack.deck_generated_at else None,
        "draft_timeline": list_draft_timeline(db, pack.project_id),
        "draft_timeline_ready": draft_ready,
        "estimated_start_date": sph.estimated_start_date.isoformat()
        if sph and sph.estimated_start_date
        else None,
        "project_timeline": draft_project_timeline_summary(
            db,
            pack.project_id,
            sph.estimated_start_date if sph else None,
        ),
        "timeline_confirmed": bool(project and project.kickoff_timeline_confirmed_at),
    }


@router.get("")
def get_pack(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.read", "decks.generate", "projects.write")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    pack = sync_pack_from_sph(db, project)
    db.commit()
    return _pack_out(db, pack)


@router.put("")
def update_pack(
    project_id: int,
    body: PreKickoffUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if project and prior_phase_data_locked(project):
        raise HTTPException(
            status_code=400,
            detail="Kick Off pack read-only — proyek sudah in delivery",
        )
    pack = db.get(PreKickoffPack, project_id)
    if not pack:
        pack = PreKickoffPack(project_id=project_id)
        db.add(pack)
    data = body.model_dump(exclude_unset=True)
    if body.deliverables_items is not None:
        pack.deliverables_items = [
            {"id": i.id or "", "text": (i.text or "").strip()}
            for i in body.deliverables_items
            if (i.text or "").strip()
        ]
        data.pop("deliverables_items", None)
    for k, v in data.items():
        setattr(pack, k, v)
    pack.is_complete = check_pack_complete(pack)
    pack.updated_at = datetime.utcnow()
    log_activity(
        db,
        project_id,
        user.id,
        "pre_kickoff.updated",
        {"fields": list(data.keys())},
    )
    db.commit()
    return _pack_out(db, pack)


@router.post("/confirm-timeline")
def confirm_timeline(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    if prior_phase_data_locked(project):
        raise HTTPException(
            status_code=400,
            detail="Konfirmasi timeline tidak tersedia — proyek sudah in delivery",
        )
    try:
        confirm_kickoff_timeline(db, project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log_activity(db, project_id, user.id, "pre_kickoff.timeline_confirmed", {})
    db.commit()
    pack = db.get(PreKickoffPack, project_id)
    if pack:
        return _pack_out(db, pack)
    return {"timeline_confirmed": True}


@router.get("/preview-deck")
def preview_pre_deck(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        dest = build_pre_kickoff_deck_path(db, project, preview=True)
        return FileResponse(
            dest,
            filename=f"Preview_Pre_Kickoff_{project.code}.pptx",
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/preview-kickoff-deck")
def preview_ko_deck(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        dest = build_kickoff_deck_path(db, project, preview=True)
        return FileResponse(
            dest,
            filename=f"Preview_Kickoff_{project.code}.pptx",
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/generate-deck")
def gen_pre_deck(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        doc = generate_pre_kickoff_deck(db, project, user.id)
        pack = db.get(PreKickoffPack, project_id)
        if pack:
            pack.is_complete = check_pack_complete(pack)
        db.commit()
        return {"document_id": doc.id, "filename": doc.filename}
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/generate-kickoff-deck")
def gen_ko_deck(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "decks.generate")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        doc = generate_kickoff_deck(db, project, user.id)
        db.commit()
        return {"document_id": doc.id, "filename": doc.filename}
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
