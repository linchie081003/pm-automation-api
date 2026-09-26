from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker
from app.database import get_db
from app.models import Permission
from app.schemas.auth import PermissionOut

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("", response_model=list[PermissionOut])
def list_permissions(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("permissions.read")),
):
    perms = db.scalars(select(Permission).order_by(Permission.module, Permission.code)).all()
    return [PermissionOut.model_validate(p) for p in perms]
