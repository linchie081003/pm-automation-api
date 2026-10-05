from unittest.mock import MagicMock

from app.models import ClickUpTaskCache
from app.services.clickup import _find_clickup_task_cache, _upsert_task_cache


def test_find_clickup_task_cache_sees_pending_session_new():
    db = MagicMock()
    db.scalar.return_value = None
    pending = ClickUpTaskCache(
        project_id=22,
        clickup_task_id="z94x8e6fe5",
        name="Task",
        status="open",
    )
    db.new = [pending]
    assert _find_clickup_task_cache(db, 22, "z94x8e6fe5") is pending


def test_upsert_task_cache_twice_same_task_id_no_second_add():
    project = MagicMock()
    project.id = 22
    db = MagicMock()
    db.scalar.return_value = None
    db.new = []

    def _track_add(obj):
        db.new.append(obj)

    db.add.side_effect = _track_add

    task = {"id": "z94x8e6fe5", "name": "Milestone task", "status": {"status": "to do"}}
    _upsert_task_cache(db, project, task, list_id="list-a", milestone_id=58)
    _upsert_task_cache(
        db,
        project,
        task,
        list_id=None,
        milestone_id=59,
        milestone_id_authoritative=True,
    )

    assert db.add.call_count == 1
    row = db.new[0]
    assert row.clickup_task_id == "z94x8e6fe5"
    assert row.milestone_id == 59
