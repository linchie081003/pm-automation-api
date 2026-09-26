"""Provision ClickUp folder → list (milestone) → task / subtask / checklist."""
from app.models import Project
from sqlalchemy.orm import Session

from app.services.clickup_hierarchy import provision_folder_structure


def provision_timeline_tasks(
    db: Session,
    project: Project,
    *,
    force: bool = False,
    auto_create_lists: bool = True,
) -> dict:
    return provision_folder_structure(
        db, project, force=force, auto_create_lists=auto_create_lists
    )
