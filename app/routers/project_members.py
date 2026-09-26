from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import Project, ProjectMember, ProjectMemberRole, User
from app.schemas.auth import ProjectMemberCreate, ProjectMemberOut

router = APIRouter(prefix="/projects/{project_id}/members", tags=["project-members"])


def _member_out(m: ProjectMember, user: User) -> ProjectMemberOut:
    return ProjectMemberOut(
        id=m.id,
        user_id=m.user_id,
        user_email=user.email,
        user_name=user.name,
        member_role=m.member_role.value,
        assigned_at=m.assigned_at.isoformat(),
    )


@router.get("", response_model=list[ProjectMemberOut])
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    rows = db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
    ).all()
    return [_member_out(m, u) for m, u in rows]


@router.post("", response_model=ProjectMemberOut)
def add_member(
    project_id: int,
    body: ProjectMemberCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.assign_members")),
):
    ensure_project_write(project_id, user, codes, db)
    if body.member_role == "owner":
        raise HTTPException(status_code=400, detail="Use transfer owner to assign owner")
    try:
        role = ProjectMemberRole(body.member_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid member_role") from exc
    if role == ProjectMemberRole.owner:
        raise HTTPException(status_code=400, detail="Cannot add owner via this endpoint")

    target = db.get(User, body.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    existing = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == body.user_id,
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="User already a member")
    m = ProjectMember(
        project_id=project_id,
        user_id=body.user_id,
        member_role=role,
        assigned_by_id=user.id,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _member_out(m, target)


@router.delete("/{member_id}")
def remove_member(
    project_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
    _: set[str] = Depends(PermissionChecker("projects.assign_members")),
):
    ensure_project_write(project_id, user, codes, db)
    m = db.get(ProjectMember, member_id)
    if not m or m.project_id != project_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if m.member_role == ProjectMemberRole.owner:
        raise HTTPException(status_code=400, detail="Cannot remove owner; transfer ownership first")
    db.delete(m)
    db.commit()
    return {"deleted": True}
