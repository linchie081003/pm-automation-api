from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.models import Project, ProjectMemberRole, User
from app.services.authorization import (
    has_permission,
    user_can_read_project,
    user_can_write_project,
    PM_MEMBER_ROLES,
    WRITE_MEMBER_ROLES,
)


def ensure_project_read(
    project_id: int,
    user: User,
    codes: set[str],
    db: Session,
) -> None:
    if not user_can_read_project(db, user, project_id, codes):
        raise HTTPException(status_code=403, detail="No access to this project")
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


def ensure_project_write(
    project_id: int,
    user: User,
    codes: set[str],
    db: Session,
    min_roles: set[ProjectMemberRole] | None = None,
) -> None:
    ensure_project_read(project_id, user, codes, db)
    roles = min_roles or PM_MEMBER_ROLES
    if user_can_write_project(db, user, project_id, codes, roles):
        return
    raise HTTPException(status_code=403, detail="Insufficient project access")


def ensure_permission(codes: set[str], *required: str) -> None:
    if not any(has_permission(codes, r) for r in required):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
