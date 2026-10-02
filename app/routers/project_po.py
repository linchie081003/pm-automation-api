from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import Project, ProjectPo, User
from app.services.project_lifecycle import po_form_editable
from app.core.timezone import now_jakarta

router = APIRouter(prefix="/projects/{project_id}/po", tags=["project-po"])


class ServiceItemIn(BaseModel):
    id: str | None = None
    name: str = ""
    qty: float = Field(default=0, ge=0)
    uom: str = ""
    target_delivery: str | None = None
    warranty: str | None = None
    unit_price: float = Field(default=0, ge=0)


class PaymentTermIn(BaseModel):
    label: str = ""
    percent_pct: float | None = None
    due_date: str | None = None


class PoUpdate(BaseModel):
    po_no: str | None = None
    po_name: str | None = Field(default=None, max_length=255)
    buyer_name: str | None = None
    contract_number: str | None = None
    quotation_reference: str | None = None
    po_due_date: date | None = None
    po_payment_terms: list[PaymentTermIn] | None = None
    service_items: list[ServiceItemIn] | None = None


def _sub_total(items: list) -> float:
    total = 0.0
    for it in items:
        if not isinstance(it, dict):
            continue
        qty = float(it.get("qty") or 0)
        price = float(it.get("unit_price") or 0)
        total += qty * price
    return round(total, 2)


def _out(row: ProjectPo, project: Project | None) -> dict:
    return {
        "project_id": row.project_id,
        "po_no": row.po_no,
        "po_name": row.po_name,
        "buyer_name": row.buyer_name,
        "contract_number": row.contract_number,
        "quotation_reference": row.quotation_reference,
        "po_due_date": row.po_due_date.isoformat() if row.po_due_date else None,
        "po_payment_terms": row.po_payment_terms or [],
        "service_items": row.service_items or [],
        "po_sub_total": row.po_sub_total,
        "project_po_due_date": project.po_due_date.isoformat()
        if project and project.po_due_date
        else None,
    }


def _get_or_create(db: Session, project_id: int) -> ProjectPo:
    row = db.get(ProjectPo, project_id)
    if not row:
        row = ProjectPo(project_id=project_id)
        db.add(row)
        db.flush()
    return row


@router.get("")
def get_po(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.read", "sph.write", "projects.write")
    ensure_project_read(project_id, user, codes, db)
    row = db.get(ProjectPo, project_id)
    project = db.get(Project, project_id)
    if not row:
        return _out(ProjectPo(project_id=project_id), project)
    return _out(row, project)


@router.put("")
def update_po(
    project_id: int,
    body: PoUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if project and not po_form_editable(project):
        raise HTTPException(
            status_code=400,
            detail="PO hanya baca — proyek sudah closed.",
        )
    row = _get_or_create(db, project_id)
    data = body.model_dump(exclude_unset=True)
    if body.service_items is not None:
        row.service_items = [
            {
                "id": i.id or "",
                "name": i.name.strip(),
                "qty": float(i.qty),
                "uom": (i.uom or "").strip(),
                "target_delivery": i.target_delivery,
                "warranty": i.warranty,
                "unit_price": float(i.unit_price),
            }
            for i in body.service_items
            if i.name.strip()
        ]
        row.po_sub_total = _sub_total(row.service_items)
        data.pop("service_items", None)
    if body.po_payment_terms is not None:
        row.po_payment_terms = [t.model_dump() for t in body.po_payment_terms]
        data.pop("po_payment_terms", None)
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        setattr(row, k, v)
    if row.po_due_date and project:
        project.po_due_date = row.po_due_date
    if project and row.po_sub_total is not None:
        project.contract_value = row.po_sub_total
    row.updated_at = now_jakarta()
    db.commit()
    db.refresh(row)
    return _out(row, project)
