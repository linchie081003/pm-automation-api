from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.data.permissions_catalog import (
    DEFAULT_ADMIN,
    DEMO_USERS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    SYSTEM_ROLES,
)
from app.models import (
    ClickUpStructureTemplate,
    IntegrationSettings,
    Permission,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Role,
    User,
)


def seed_rbac(db: Session) -> None:
    existing = db.scalar(select(Permission).limit(1))
    if existing:
        return

    perm_by_code: dict[str, Permission] = {}
    for code, name, module, description in PERMISSIONS:
        p = Permission(code=code, name=name, module=module, description=description)
        db.add(p)
        perm_by_code[code] = p
    db.flush()

    role_by_code: dict[str, Role] = {}
    for code, name, description in SYSTEM_ROLES:
        role = Role(
            code=code,
            name=name,
            description=description,
            is_system=True,
        )
        db.add(role)
        role_by_code[code] = role
    db.flush()

    for role_code, perm_codes in ROLE_PERMISSIONS.items():
        role = role_by_code[role_code]
        for pc in perm_codes:
            perm = perm_by_code.get(pc)
            if perm and perm not in role.permissions:
                role.permissions.append(perm)

    admin_user = User(
        email=DEFAULT_ADMIN["email"],
        name=DEFAULT_ADMIN["name"],
        hashed_password=hash_password(DEFAULT_ADMIN["password"]),
        is_active=True,
    )
    db.add(admin_user)
    db.flush()
    admin_user.roles.append(role_by_code[DEFAULT_ADMIN["role_code"]])

    for email, password, name, role_code in DEMO_USERS:
        u = User(
            email=email,
            name=name,
            hashed_password=hash_password(password),
            is_active=True,
        )
        db.add(u)
        db.flush()
        u.roles.append(role_by_code[role_code])

    db.commit()


def sync_permission_catalog(db: Session) -> None:
    """Add new permissions and attach to roles per ROLE_PERMISSIONS (existing DBs)."""
    perm_by_code: dict[str, Permission] = {
        p.code: p for p in db.scalars(select(Permission)).all()
    }
    changed = False
    for code, name, module, description in PERMISSIONS:
        if code not in perm_by_code:
            p = Permission(code=code, name=name, module=module, description=description)
            db.add(p)
            perm_by_code[code] = p
            changed = True
    if changed:
        db.flush()
    roles = {r.code: r for r in db.scalars(select(Role)).all()}
    for role_code, perm_codes in ROLE_PERMISSIONS.items():
        role = roles.get(role_code)
        if not role:
            continue
        existing = {p.code for p in role.permissions}
        for pc in perm_codes:
            perm = perm_by_code.get(pc)
            if perm and pc not in existing:
                role.permissions.append(perm)
                changed = True
    if changed:
        db.commit()


def ensure_org_defaults(db: Session) -> None:
    changed = False
    if not db.scalar(select(IntegrationSettings).limit(1)):
        db.add(IntegrationSettings(clickup_offer_on_kickoff=True))
        changed = True
    if not db.scalar(select(ClickUpStructureTemplate).limit(1)):
        db.add(
            ClickUpStructureTemplate(
                name="Standard Delivery",
                is_default=True,
                definition={"tasks": [{"name": "Development"}, {"name": "UAT"}]},
            )
        )
        changed = True
    if changed:
        db.commit()


def ensure_project_owner_member(db: Session, project: Project) -> None:
    existing = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == project.owner_id,
        )
    )
    if not existing:
        db.add(
            ProjectMember(
                project_id=project.id,
                user_id=project.owner_id,
                member_role=ProjectMemberRole.owner,
            )
        )


def backfill_project_members(db: Session) -> None:
    projects = db.scalars(select(Project)).all()
    for p in projects:
        ensure_project_owner_member(db, p)
    db.commit()
