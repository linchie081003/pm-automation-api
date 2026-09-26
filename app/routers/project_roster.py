from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ProjectRosterEntry, User

router = APIRouter(prefix="/projects/{project_id}/roster", tags=["project-roster"])


class RosterCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(default="", max_length=255)
    role_label: str = Field(default="", max_length=128)


class RosterPatch(BaseModel):
    full_name: str | None = None
    email: str | None = None
    role_label: str | None = None


def _out(r: ProjectRosterEntry) -> dict:
    return {
        "id": r.id,
        "full_name": r.full_name,
        "email": r.email,
        "role_label": r.role_label,
        "created_at": r.created_at.isoformat(),
    }


@router.get("")
def list_roster(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    rows = db.scalars(
        select(ProjectRosterEntry)
        .where(ProjectRosterEntry.project_id == project_id)
        .order_by(ProjectRosterEntry.id)
    ).all()
    return [_out(r) for r in rows]


@router.post("")
def add_roster(
    project_id: int,
    body: RosterCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.assign_members", "projects.write")),
):
    ensure_project_write(project_id, user, codes, db)
    r = ProjectRosterEntry(
        project_id=project_id,
        full_name=body.full_name.strip(),
        email=(body.email or "").strip(),
        role_label=(body.role_label or "").strip(),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _out(r)


@router.patch("/{entry_id}")
def patch_roster(
    project_id: int,
    entry_id: int,
    body: RosterPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.assign_members", "projects.write")),
):
    ensure_project_write(project_id, user, codes, db)
    r = db.get(ProjectRosterEntry, entry_id)
    if not r or r.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None and isinstance(v, str):
            v = v.strip()
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _out(r)


@router.delete("/{entry_id}")
def delete_roster(
    project_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.assign_members", "projects.write")),
):
    ensure_project_write(project_id, user, codes, db)
    r = db.get(ProjectRosterEntry, entry_id)
    if not r or r.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(r)
    db.commit()
    return {"deleted": True}
