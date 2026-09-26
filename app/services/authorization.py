from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Project, ProjectMember, ProjectMemberRole, Role, User

WRITE_MEMBER_ROLES = {
    ProjectMemberRole.owner,
    ProjectMemberRole.pm,
    ProjectMemberRole.delivery,
}
PM_MEMBER_ROLES = {ProjectMemberRole.owner, ProjectMemberRole.pm}


def load_user_permissions(db: Session, user: User) -> set[str]:
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    if not user:
        return set()
    codes: set[str] = set()
    for role in user.roles:
        if role.code == "admin":
            return {"*"}
        for perm in role.permissions:
            codes.add(perm.code)
    return codes


def has_permission(codes: set[str], required: str) -> bool:
    if "*" in codes:
        return True
    if required in codes:
        return True
    prefix = required.rsplit(".", 1)[0]
    return f"{prefix}.*" in codes


def has_any_permission(codes: set[str], required: Iterable[str]) -> bool:
    return any(has_permission(codes, r) for r in required)


def user_can_read_project(
    db: Session, user: User, project_id: int, perm_codes: set[str]
) -> bool:
    if "*" in perm_codes:
        return True
    if has_permission(perm_codes, "projects.read.all"):
        return True
    if not has_permission(perm_codes, "projects.read.own"):
        return False
    project = db.get(Project, project_id)
    if project and project.owner_id == user.id:
        return True
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    return member is not None


def user_can_write_project(
    db: Session,
    user: User,
    project_id: int,
    perm_codes: set[str],
    min_role: set[ProjectMemberRole] | None = None,
) -> bool:
    if "*" in perm_codes:
        return True
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if member:
        allowed = min_role or PM_MEMBER_ROLES
        return member.member_role in allowed
    project = db.get(Project, project_id)
    return bool(project and project.owner_id == user.id)


def filter_projects_for_user(db: Session, user: User, perm_codes: set[str]) -> list[Project]:
    if has_permission(perm_codes, "projects.read.all"):
        return list(db.scalars(select(Project)).all())
    q_own = select(Project).where(Project.owner_id == user.id)
    member_ids = db.scalars(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    ).all()
    if member_ids:
        q_member = select(Project).where(Project.id.in_(member_ids))
        owned = {p.id: p for p in db.scalars(q_own).all()}
        for p in db.scalars(q_member).all():
            owned[p.id] = p
        return list(owned.values())
    return list(db.scalars(q_own).all())
