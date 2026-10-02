from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import Project, ProjectPhase, ScheduleBaselineMilestone, User
from app.services.activity import log_activity
from app.services.project_lifecycle import (
    draft_timeline_editable,
    kickoff_draft_timeline_editable,
    sph_form_editable,
    sph_timeline_editable,
)
from app.services.workflow import apply_phase_transition, next_phase
from app.services.draft_timeline import (
    list_draft_rows,
    preview_recalc_draft_rows,
    save_draft_rows,
)
from app.services.timeline_schedule import compute_project_timeline_summary
from app.core.timezone import now_jakarta
from app.services.sph import (
    apply_sph_total_from_delivery,
    compute_payment_term_amounts,
    delivery_total_rupiah,
    finalize_sph_timeline_for_kickoff,
    generate_draft_timeline_from_template,
    get_or_create_sph,
    rescale_existing_draft_to_target,
    sph_is_complete,
    sync_payment_terms_with_draft_timeline,
    sync_sph_text_from_items,
)

router = APIRouter(prefix="/projects/{project_id}/sph", tags=["sph"])


class LineItemIn(BaseModel):
    id: str | None = None
    module: str = ""
    text: str = Field(min_length=1)


class DeliveryItemIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    amount_rupiah: float = Field(ge=0)


class PaymentTermIn(BaseModel):
    label: str = ""
    due_date: str | None = None
    percent_pct: float | None = Field(default=None, ge=0, le=100)
    amount: float | None = None
    draft_milestone_id: int | None = None
    draft_milestone_row_key: str | None = None


class DraftRowIn(BaseModel):
    id: int | None = None
    row_key: str | None = None
    name: str
    duration_days: int | None = 1
    weight_pct: float = 0
    is_payment_milestone: bool = False
    item_type: str = "milestone"
    parent_ref: str | None = None
    parent_id: int | None = None
    sort_order: int = 0
    start_date: date | None = None
    target_date: date | None = None
    predecessor_ref: str | None = None
    predecessor_link_type: str | None = None
    schedule_driver: str | None = None


class DraftTimelineUpdate(BaseModel):
    start_date: date | None = None
    rows: list[DraftRowIn]


class SphUpdate(BaseModel):
    sph_no: str | None = None
    sph_name: str | None = None
    sph_client: str | None = None
    sales_pic: str | None = None
    estimated_start_date: date | None = None
    timeline_template_id: int | None = None
    target_delivery_days: int | None = None
    scope_items: list[LineItemIn] | None = None
    non_scope_text: str | None = None
    non_scope_items: list[LineItemIn] | None = None
    delivery_items: list[DeliveryItemIn] | None = None
    delivery_method: str | None = None
    pic_user_name: str | None = None
    pic_user_contact: str | None = None
    planned_md: float | None = None


class PaymentTermsUpdate(BaseModel):
    payment_terms: list[PaymentTermIn]


class GenerateDraftBody(BaseModel):
    start_date: date | None = None
    timeline_template_id: int | None = None


def _normalize_line_items(items: list[LineItemIn] | None) -> list[dict]:
    if not items:
        return []
    return [
        {"id": i.id or "", "module": (i.module or "").strip(), "text": i.text.strip()}
        for i in items
        if i.text.strip()
    ]


def _normalize_delivery(items: list[DeliveryItemIn] | None) -> list[dict]:
    if not items:
        return []
    return [
        {
            "id": i.id or "",
            "name": i.name.strip(),
            "amount_rupiah": float(i.amount_rupiah),
        }
        for i in items
        if i.name.strip()
    ]


def _resolve_payment_terms(db: Session, project_id: int, terms: list[PaymentTermIn]) -> list[dict]:
    out: list[dict] = []
    for t in terms:
        row = t.model_dump()
        if t.draft_milestone_row_key:
            row["draft_milestone_row_key"] = t.draft_milestone_row_key
        if t.draft_milestone_id:
            bl_row = db.get(ScheduleBaselineMilestone, t.draft_milestone_id)
            if bl_row and bl_row.target_date:
                row["due_date"] = bl_row.target_date.isoformat()
                if not row.get("label"):
                    row["label"] = bl_row.name
                if bl_row.row_key:
                    row["draft_milestone_row_key"] = bl_row.row_key
        out.append(row)
    return out


def _out(sph) -> dict:
    sync_sph_text_from_items(sph)
    terms = compute_payment_term_amounts(sph.payment_terms or [], sph.sph_total_rupiah)
    return {
        "project_id": sph.project_id,
        "sph_no": sph.sph_no,
        "sph_name": sph.sph_name,
        "sph_client": sph.sph_client,
        "sales_pic": sph.sales_pic,
        "estimated_start_date": sph.estimated_start_date.isoformat()
        if sph.estimated_start_date
        else None,
        "target_delivery_days": sph.target_delivery_days,
        "scope_text": sph.scope_text or "",
        "non_scope_text": sph.non_scope_text or "",
        "scope_items": sph.scope_items or [],
        "non_scope_items": sph.non_scope_items or [],
        "delivery_items": sph.delivery_items or [],
        "sph_total_rupiah": sph.sph_total_rupiah,
        "delivery_total_rupiah": delivery_total_rupiah(sph),
        "payment_terms": terms,
        "delivery_method": sph.delivery_method,
        "pic_user_name": sph.pic_user_name,
        "pic_user_contact": sph.pic_user_contact,
        "planned_md": sph.planned_md,
        "draft_baseline_generated_at": sph.draft_baseline_generated_at.isoformat()
        if sph.draft_baseline_generated_at
        else None,
        "timeline_template_id": sph.timeline_template_id,
        "is_complete": sph_is_complete(sph),
    }


def _out_with_draft(db: Session, sph) -> dict:
    data = _out(sph)
    data["draft_timeline"] = list_draft_rows(db, sph.project_id)
    data["project_timeline"] = compute_project_timeline_summary(
        db,
        data["draft_timeline"],
        sph.estimated_start_date,
    )
    return data


@router.get("")
def get_sph(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.read", "sph.write", "projects.write")
    ensure_project_read(project_id, user, codes, db)
    sph = get_or_create_sph(db, project_id)
    db.commit()
    return _out_with_draft(db, sph)


@router.put("")
def update_sph(
    project_id: int,
    body: SphUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    sph = get_or_create_sph(db, project_id)
    project = db.get(Project, project_id)
    data = body.model_dump(exclude_unset=True)
    kickoff_start_only = (
        project
        and not sph_form_editable(project, sph)
        and kickoff_draft_timeline_editable(project, sph=sph)
        and set(data.keys()) <= {"estimated_start_date"}
    )
    if project and not sph_form_editable(project, sph) and not kickoff_start_only:
        raise HTTPException(
            status_code=400,
            detail="SPH read-only — sudah lanjut ke fase Kick Off",
        )
    if body.scope_items is not None:
        sph.scope_items = _normalize_line_items(body.scope_items)
        data.pop("scope_items", None)
    if body.non_scope_text is not None:
        sph.non_scope_text = (body.non_scope_text or "").strip() or None
        sph.non_scope_items = []
        data.pop("non_scope_text", None)
    elif body.non_scope_items is not None:
        sph.non_scope_items = _normalize_line_items(body.non_scope_items)
        data.pop("non_scope_items", None)
    if body.delivery_items is not None:
        sph.delivery_items = _normalize_delivery(body.delivery_items)
        apply_sph_total_from_delivery(sph)
        data.pop("delivery_items", None)
    for k, v in data.items():
        setattr(sph, k, v)
    sync_sph_text_from_items(sph)
    project = db.get(Project, project_id)
    if (
        project
        and "target_delivery_days" in data
        and sph.target_delivery_days
        and int(sph.target_delivery_days) > 0
        and sph_timeline_editable(project, sph=sph)
        and not kickoff_draft_timeline_editable(project, sph=sph)
    ):
        try:
            rescale_existing_draft_to_target(db, project, int(sph.target_delivery_days))
            sync_payment_terms_with_draft_timeline(db, sph)
        except ValueError:
            pass
    if project:
        if sph.sph_name and str(sph.sph_name).strip():
            project.name = str(sph.sph_name).strip()
        if sph.sph_client is not None and str(sph.sph_client).strip():
            project.client_name = str(sph.sph_client).strip()
    sph.updated_at = now_jakarta()
    log_activity(
        db,
        project_id,
        user.id,
        "sph.updated",
        {"fields": list(body.model_dump(exclude_unset=True).keys())},
    )
    db.commit()
    return _out_with_draft(db, sph)


@router.post("/draft-timeline/recalc")
def recalc_draft_timeline_preview(
    project_id: int,
    body: DraftTimelineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    """Hitung ulang mulai/selesai dari durasi + kalender kerja (tidak menyimpan)."""
    ensure_permission(codes, "sph.read", "sph.write", "projects.read.all", "projects.read.own")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    sph = get_or_create_sph(db, project_id)
    try:
        eff = body.start_date or sph.estimated_start_date
        rows = preview_recalc_draft_rows(
            db,
            project,
            [r.model_dump() for r in body.rows],
            eff,
        )
        return {
            "draft_timeline": rows,
            "project_timeline": compute_project_timeline_summary(db, rows, eff),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/draft-timeline")
def update_draft_timeline(
    project_id: int,
    body: DraftTimelineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    sph = get_or_create_sph(db, project_id)
    if not draft_timeline_editable(project, sph=sph):
        raise HTTPException(
            status_code=400,
            detail="Timeline draft tidak dapat diedit pada fase ini",
        )
    try:
        eff = body.start_date or sph.estimated_start_date
        rows = save_draft_rows(
            db,
            project,
            [r.model_dump() for r in body.rows],
            eff,
        )
        sync_payment_terms_with_draft_timeline(db, sph)
        sph.updated_at = now_jakarta()
        log_activity(
            db,
            project_id,
            user.id,
            "sph.draft_timeline.saved",
            {"rows": len(rows)},
        )
        db.commit()
        terms = compute_payment_term_amounts(sph.payment_terms or [], sph.sph_total_rupiah)
        return {
            "draft_timeline": rows,
            "payment_terms": terms,
            "project_timeline": compute_project_timeline_summary(db, rows, eff),
        }
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/payment-terms")
def update_payment_terms(
    project_id: int,
    body: PaymentTermsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    sph = get_or_create_sph(db, project_id)
    project = db.get(Project, project_id)
    if project and not sph_form_editable(project, sph):
        raise HTTPException(
            status_code=400,
            detail="SPH read-only — sudah lanjut ke fase Kick Off",
        )
    apply_sph_total_from_delivery(sph)
    if not sph.sph_total_rupiah:
        raise HTTPException(
            status_code=400,
            detail="Total SPH belum ada — isi item delivery di SPH terlebih dahulu",
        )
    raw_terms = _resolve_payment_terms(db, project_id, body.payment_terms)
    sph.payment_terms = compute_payment_term_amounts(raw_terms, sph.sph_total_rupiah)
    sph.updated_at = now_jakarta()
    db.commit()
    return _out(sph)


@router.post("/generate-draft-timeline")
def generate_draft(
    project_id: int,
    body: GenerateDraftBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    sph = get_or_create_sph(db, project_id)
    if not sph_timeline_editable(project, sph=sph):
        raise HTTPException(
            status_code=400,
            detail="Generate draft hanya di fase SPH — sesuaikan timeline di tab Kick Off",
        )
    try:
        start = body.start_date if body else None
        tpl_id = body.timeline_template_id if body else None
        baseline = generate_draft_timeline_from_template(
            db,
            project,
            user.id,
            start_date=start,
            timeline_template_id=tpl_id,
        )
        sph = get_or_create_sph(db, project_id)
        sync_payment_terms_with_draft_timeline(db, sph)
        log_activity(
            db,
            project_id,
            user.id,
            "sph.draft_timeline.generated",
            {"baseline_version": baseline.version},
        )
        db.commit()
        return {"baseline_version": baseline.version, "is_draft": baseline.is_draft}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Konflik versi baseline. Muat ulang halaman, lalu generate draft sekali lagi.",
        ) from e
    except Exception as e:
        db.rollback()
        raise


@router.post("/generate-timeline")
def finalize_timeline_for_kickoff(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        finalize_sph_timeline_for_kickoff(db, project)
        target = next_phase(project.current_phase)
        if target == ProjectPhase.kickoff:
            apply_phase_transition(db, project, ProjectPhase.kickoff)
        log_activity(db, project_id, user.id, "sph.advance_kickoff", {})
        db.commit()
        sph = get_or_create_sph(db, project_id)
        return {
            "ok": True,
            "current_phase": project.current_phase.value,
            "draft_baseline_generated_at": sph.draft_baseline_generated_at.isoformat()
            if sph.draft_baseline_generated_at
            else None,
            "draft_timeline": list_draft_rows(db, project_id),
        }
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
