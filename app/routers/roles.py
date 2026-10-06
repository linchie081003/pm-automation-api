from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import PermissionChecker
from app.database import get_db
from app.models import Permission, Role
from app.models.auth import user_roles
from app.schemas.auth import (
    PermissionOut,
    RoleCreate,
    RoleOut,
    RolePermissionsUpdate,
    RoleUpdate,
)

router = APIRouter(prefix="/roles", tags=["roles"])


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permission_codes=sorted(p.code for p in role.permissions),
    )


@router.get("/permissions/catalog", response_model=list[PermissionOut])
def list_permissions(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("permissions.read")),
):
    perms = db.scalars(select(Permission).order_by(Permission.module, Permission.code)).all()
    return [PermissionOut.model_validate(p) for p in perms]


@router.get("", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("roles.read")),
):
    roles = db.scalars(
        select(Role).options(selectinload(Role.permissions)).order_by(Role.code)
    ).all()
    return [_role_out(r) for r in roles]


@router.post("", response_model=RoleOut)
def create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("roles.write")),
):
    if db.scalar(select(Role).where(Role.code == body.code)):
        raise HTTPException(status_code=400, detail="Role code exists")
    role = Role(
        code=body.code,
        name=body.name,
        description=body.description,
        is_system=False,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_out(role)


@router.patch("/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("roles.write")),
):
    role = db.scalar(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    db.commit()
    db.refresh(role)
    return _role_out(role)


@router.put("/{role_id}/permissions", response_model=RoleOut)
def set_role_permissions(
    role_id: int,
    body: RolePermissionsUpdate,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("roles.write")),
):
    role = db.scalar(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    perms = db.scalars(
        select(Permission).where(Permission.code.in_(body.permission_codes))
    ).all()
    found = {p.code for p in perms}
    missing = set(body.permission_codes) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown permissions: {missing}")
    role.permissions = list(perms)
    db.commit()
    db.refresh(role)
    return _role_out(role)


@router.delete("/{role_id}")
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("roles.write")),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system role")
    user_count = db.scalar(
        select(func.count())
        .select_from(user_roles)
        .where(user_roles.c.role_id == role_id)
    )
    if user_count:
        raise HTTPException(
            status_code=400,
            detail=f"Role masih dipakai {user_count} user. Pindahkan atau hapus assignment dulu.",
        )
    db.delete(role)
    db.commit()
    return {"deleted": True}
