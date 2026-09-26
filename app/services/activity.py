from sqlalchemy.orm import Session

from app.models import ActivityLog


def log_activity(
    db: Session,
    project_id: int | None,
    user_id: int | None,
    action: str,
    detail: dict | None = None,
) -> None:
    db.add(
        ActivityLog(
            project_id=project_id,
            user_id=user_id,
            action=action,
            detail=detail or {},
        )
    )
