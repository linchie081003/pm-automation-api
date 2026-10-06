from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import PermissionChecker, get_current_user
from app.core.security import hash_password
from app.database import get_db
from app.models import Role, User
from app.models.auth import user_roles
from app.schemas.auth import RoleBrief, UserCreate, UserOut, UserUpdate
from app.services.refresh_tokens import revoke_all_user_refresh_sessions

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        last_login=user.last_login,
        roles=[RoleBrief.model_validate(r) for r in user.roles],
    )


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("users.read")),
):
    users = db.scalars(select(User).options(selectinload(User.roles))).all()
    return [_user_out(u) for u in users]


@router.post("", response_model=UserOut)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("users.write")),
):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
        is_active=body.is_active,
    )
    db.add(user)
    db.flush()
    if body.role_ids:
        roles = db.scalars(select(Role).where(Role.id.in_(body.role_ids))).all()
        user.roles = list(roles)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("users.write")),
):
    user = db.scalar(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_active is False and user.id == current.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    if body.role_ids is not None:
        admin_role = db.scalar(select(Role).where(Role.code == "admin"))
        if admin_role and admin_role in user.roles:
            admin_count = db.scalar(
                select(func.count())
                .select_from(user_roles)
                .where(user_roles.c.role_id == admin_role.id)
            )
            removing_admin = admin_role.id not in body.role_ids
            if removing_admin and admin_count <= 1:
                raise HTTPException(status_code=400, detail="Cannot remove last admin")
        roles = db.scalars(select(Role).where(Role.id.in_(body.role_ids))).all()
        user.roles = list(roles)

    if body.name is not None:
        user.name = body.name
    if body.is_active is not None:
        user.is_active = body.is_active
        if body.is_active is False:
            revoke_all_user_refresh_sessions(db, user.id)
    if body.password:
        user.hashed_password = hash_password(body.password)
        revoke_all_user_refresh_sessions(db, user.id)

    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("users.write")),
):
    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"deleted": True}
