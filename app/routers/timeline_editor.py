from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_permission_codes
from app.database import get_db
from app.models import User
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.services.activity import log_activity
from app.services.timeline_editor import (
    timeline_editor_recalc,
    timeline_editor_save,
    timeline_editor_snapshot,
)
from app.services.timeline_editor_store import EditorWorkspaceConflictError
router = APIRouter(prefix="/projects", tags=["timeline-editor"])


class TimelineEditorPredecessorIn(BaseModel):
    predecessor_ref: str
    link_type: str = "FS"
    lag_days: int = Field(default=0, ge=0)


class TimelineEditorRowIn(BaseModel):
    id: int | None = None
    row_key: str | None = None
    name: str = ""
    duration_days: int | None = 1
    weight_pct: float = 0
    item_type: str = "phase"
    parent_ref: str | None = None
    parent_id: int | None = None
    sort_order: int = 0
    start_date: str | None = None
    target_date: str | None = None
    predecessor_ref: str | None = None
    predecessor_link_type: str | None = None
    schedule_driver: str | None = None
    predecessors: list[TimelineEditorPredecessorIn] = Field(default_factory=list)


class TimelineEditorRecalcBody(BaseModel):
    start_date: date | None = None
    rows: list[TimelineEditorRowIn]
    workspace_updated_at: datetime | None = None


@router.get("/{project_id}/timeline-editor/snapshot")
def get_timeline_editor_snapshot(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.read", "sph.write", "projects.read.all", "projects.read.own")
    ensure_project_read(project_id, user, codes, db)
    try:
        return timeline_editor_snapshot(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{project_id}/timeline-editor/recalc")
def post_timeline_editor_recalc(
    project_id: int,
    body: TimelineEditorRecalcBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.read", "sph.write", "projects.read.all", "projects.read.own")
    ensure_project_read(project_id, user, codes, db)
    try:
        rows = [r.model_dump() for r in body.rows]
        return timeline_editor_recalc(db, project_id, rows, body.start_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/{project_id}/timeline-editor/save")
def put_timeline_editor_save(
    project_id: int,
    body: TimelineEditorRecalcBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "sph.write", "projects.write")
    ensure_project_write(project_id, user, codes, db)
    try:
        rows = [r.model_dump() for r in body.rows]
        out = timeline_editor_save(
            db,
            project_id,
            rows,
            body.start_date,
            expected_workspace_updated_at=body.workspace_updated_at,
        )
        log_activity(
            db,
            project_id,
            user.id,
            "timeline_editor.saved",
            {"rows": len(out.get("rows") or [])},
        )
        db.commit()
        return out
    except EditorWorkspaceConflictError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
