from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.security import decode_token
from app.database import get_db
from app.models import ProjectMemberRole, User
from app.services.authorization import (
    has_any_permission,
    has_permission,
    load_user_permissions,
    user_can_read_project,
    user_can_write_project,
)

security = HTTPBearer(auto_error=False)


def get_current_user_optional(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Session = Depends(get_db),
) -> User | None:
    if not creds:
        return None
    try:
        payload = decode_token(creds.credentials)
        if payload.get("type") != "access":
            return None
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.roles))
    )
    if not user or not user.is_active:
        return None
    return user


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def get_permission_codes(
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> set[str]:
    return load_user_permissions(db, user)


class PermissionChecker:
    def __init__(self, *required: str):
        self.required = required

    def __call__(
        self,
        codes: Annotated[set[str], Depends(get_permission_codes)],
    ) -> set[str]:
        if not has_any_permission(codes, self.required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return codes


def require_project_read(project_id: int):
    def _checker(
        user: Annotated[User, Depends(get_current_user)],
        codes: Annotated[set[str], Depends(get_permission_codes)],
        db: Session = Depends(get_db),
    ) -> User:
        if not user_can_read_project(db, user, project_id, codes):
            raise HTTPException(status_code=403, detail="No access to this project")
        return user

    return _checker


def require_project_write(
    project_id: int,
    min_role: set[ProjectMemberRole] | None = None,
):
    def _checker(
        user: Annotated[User, Depends(get_current_user)],
        codes: Annotated[set[str], Depends(get_permission_codes)],
        db: Session = Depends(get_db),
    ) -> User:
        if not user_can_read_project(db, user, project_id, codes):
            raise HTTPException(status_code=403, detail="No access to this project")
        if min_role and user_can_write_project(db, user, project_id, codes, min_role):
            return user
        if user_can_write_project(db, user, project_id, codes):
            return user
        raise HTTPException(status_code=403, detail="Insufficient project access")

    return _checker
