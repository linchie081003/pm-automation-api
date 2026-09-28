from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker
from app.database import get_db
from app.models import ProjectMethodology, TimelineTemplate
from app.services.timeline_seed import ensure_default_timeline_templates
from app.services.timeline_template_migrate import normalize_template_items_list
from app.services.timeline_validation import validate_timeline_items

router = APIRouter(prefix="/timeline-templates", tags=["timeline-templates"])


class TemplateItemIn(BaseModel):
    row_key: str = ""
    name: str = Field(min_length=1)
    duration_days: int = Field(ge=0, default=1)
    weight_pct: float = 0
    item_type: str = "phase"
    parent_key: str | None = None
    sort_order: int = 0

    @model_validator(mode="after")
    def normalize_by_type(self) -> "TemplateItemIn":
        t = (self.item_type or "phase").strip().lower()
        if t == "milestone":
            self.duration_days = 0
            self.weight_pct = 0.0
        elif self.duration_days < 1:
            self.duration_days = 1
        return self


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    methodology: str
    description: str | None = None
    items: list[TemplateItemIn] = []


class TemplatePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    items: list[TemplateItemIn] | None = None
    is_active: bool | None = None


def _out(t: TimelineTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "methodology": t.methodology.value,
        "description": t.description,
        "items": normalize_template_items_list(t.items or []),
        "is_active": t.is_active,
        "created_at": t.created_at.isoformat(),
    }


@router.get("")
def list_templates(
    methodology: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("projects.read.all", "projects.read.own", "sph.read")),
):
    q = select(TimelineTemplate)
    if not include_inactive:
        q = q.where(TimelineTemplate.is_active.is_(True))
    if methodology:
        try:
            meth = ProjectMethodology(methodology)
            q = q.where(TimelineTemplate.methodology == meth)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid methodology") from exc
    rows = db.scalars(q.order_by(TimelineTemplate.methodology, TimelineTemplate.name)).all()
    return [_out(t) for t in rows]


@router.post("/seed-defaults")
def seed_defaults(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    ensure_default_timeline_templates(db)
    return {"seeded": True}


@router.get("/{template_id}")
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("projects.read.all", "projects.read.own", "sph.read")),
):
    t = db.get(TimelineTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    return _out(t)


@router.post("")
def create_template(
    body: TemplateCreate,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    try:
        meth = ProjectMethodology(body.methodology)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid methodology") from exc
    item_dicts = [i.model_dump() for i in body.items]
    try:
        validate_timeline_items(item_dicts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    t = TimelineTemplate(
        name=body.name.strip(),
        methodology=meth,
        description=body.description,
        items=item_dicts,
        is_active=True,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.patch("/{template_id}")
def patch_template(
    template_id: int,
    body: TemplatePatch,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    t = db.get(TimelineTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if body.items is not None:
        item_dicts = [i.model_dump() for i in body.items]
        try:
            validate_timeline_items(item_dicts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        t.items = item_dicts
        data.pop("items", None)
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    t = db.get(TimelineTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    t.is_active = False
    db.commit()
    return {"deleted": True, "is_active": False}
