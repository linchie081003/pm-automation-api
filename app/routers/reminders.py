from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read
from app.database import get_db
from app.models import Project, User
from app.services.authorization import filter_projects_for_user
from app.services.project_phases import visible_on_dashboard
from app.services.reminders import project_reminders

router = APIRouter(tags=["reminders"])


@router.get("/projects/{project_id}/reminders")
def get_project_reminders(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.read.own", "projects.read.all")
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    return project_reminders(db, project)  # type: ignore[arg-type]


@router.get("/dashboard/reminders")
def dashboard_reminders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "projects.read.own", "projects.read.all")
    projects = filter_projects_for_user(db, user, codes)
    items = []
    for p in projects:
        if not visible_on_dashboard(p):
            continue
        for r in project_reminders(db, p):
            items.append({**r, "project_id": p.id, "project_code": p.code})
    return items
