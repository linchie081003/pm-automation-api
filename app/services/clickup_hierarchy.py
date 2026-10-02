"""ClickUp: Folder (project) → List (phase) → Task → Subtask."""
from __future__ import annotations

from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Milestone, Project, TimelineItemType
from app.services.clickup import (
    ClickUpSyncError,
    _headers,
    _resolve_space_id,
    assert_clickup_folder_access,
    get_integration,
    is_configured,
)
from app.services.schedule_window import kickoff_milestones
from app.core.timezone import now_jakarta


def _folder_name(project: Project) -> str:
    name = (project.name or project.code or "Project").strip()
    code = (project.code or "").strip()
    if code and code not in name:
        return f"{name} ({code})"[:255]
    return name[:255]


def ensure_project_clickup_folder(db: Session, project: Project) -> str:
    if project.clickup_folder_id:
        return project.clickup_folder_id
    if not project.clickup_enabled:
        raise ClickUpSyncError("Aktifkan ClickUp di proyek sebelum membuat folder.")
    row = get_integration(db)
    if not row or not is_configured(db):
        raise ClickUpSyncError("Integrasi ClickUp org belum lengkap.")

    space_id = _resolve_space_id(db, project, row)
    url = f"https://api.clickup.com/api/v2/space/{space_id}/folder"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=_headers(row), json={"name": _folder_name(project)})
        resp.raise_for_status()
        data = resp.json()

    folder_id = str(data.get("id", ""))
    if not folder_id:
        raise ClickUpSyncError("ClickUp tidak mengembalikan folder ID.")

    project.clickup_folder_id = folder_id
    project.clickup_space_id = space_id
    project.clickup_list_id = None
    project.clickup_list_name = None
    db.flush()
    return folder_id


def _ms_timestamp(d) -> int | None:
    if not d:
        return None
    return int(datetime.combine(d, datetime.min.time()).timestamp() * 1000)


def _top_level_phases(all_rows: list[Milestone]) -> list[Milestone]:
    phases = [
        m
        for m in all_rows
        if m.parent_id is None and m.item_type == TimelineItemType.phase
    ]
    if phases:
        return phases
    return [
        m
        for m in all_rows
        if m.parent_id is None
        and m.item_type in (TimelineItemType.milestone, TimelineItemType.phase)
    ]


def parent_phases_for_clickup(db: Session, project_id: int) -> list[Milestone]:
    all_rows = kickoff_milestones(db, project_id)
    return sorted(_top_level_phases(all_rows), key=lambda m: (m.sort_order, m.id))


def parent_milestones_for_clickup(db: Session, project_id: int) -> list[Milestone]:
    """Backward-compatible alias."""
    return parent_phases_for_clickup(db, project_id)


def _phase_tasks(all_rows: list[Milestone], phase_id: int) -> list[Milestone]:
    return sorted(
        [
            m
            for m in all_rows
            if m.parent_id == phase_id and m.item_type == TimelineItemType.task
        ],
        key=lambda x: (x.sort_order, x.id),
    )


def _task_subtasks(all_rows: list[Milestone], task_id: int) -> list[Milestone]:
    return sorted(
        [
            m
            for m in all_rows
            if m.parent_id == task_id and m.item_type == TimelineItemType.subtask
        ],
        key=lambda x: (x.sort_order, x.id),
    )


def _list_name_key(name: str) -> str:
    return name.strip().casefold()


def _fetch_folder_lists_map(
    client: httpx.Client, headers: dict, folder_id: str
) -> dict[str, str]:
    url = f"https://api.clickup.com/api/v2/folder/{folder_id}/list"
    resp = client.get(url, headers=headers)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for x in resp.json().get("lists") or []:
        if not x.get("id"):
            continue
        key = _list_name_key(str(x.get("name") or ""))
        if key:
            out[key] = str(x["id"])
    return out


def _ensure_list_in_folder(
    client: httpx.Client,
    headers: dict,
    folder_id: str,
    name: str,
    lists_by_name: dict[str, str],
) -> tuple[str, bool]:
    """Return (list_id, created_new). Reuse existing list if name already taken."""
    display = name[:255]
    key = _list_name_key(display)
    if key and key in lists_by_name:
        return lists_by_name[key], False

    url = f"https://api.clickup.com/api/v2/folder/{folder_id}/list"
    resp = client.post(url, headers=headers, json={"name": display})
    if resp.status_code == 400:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = str(body.get("err") or "").lower()
        ecode = str(body.get("ECODE") or "")
        if "name taken" in err or ecode == "SUBCAT_016":
            lists_by_name.update(_fetch_folder_lists_map(client, headers, folder_id))
            if key and key in lists_by_name:
                return lists_by_name[key], False
            raise ClickUpSyncError(
                f"Nama list «{display}» sudah dipakai di folder ClickUp lain — rename phase timeline."
            )
    resp.raise_for_status()
    lid = str(resp.json().get("id", ""))
    if not lid:
        raise ClickUpSyncError("Gagal membuat list ClickUp.")
    if key:
        lists_by_name[key] = lid
    return lid, True


def _phase_children(all_rows: list[Milestone], phase_id: int) -> list[Milestone]:
    return sorted(
        [m for m in all_rows if m.parent_id == phase_id],
        key=lambda x: (x.sort_order, x.id),
    )


def _create_task_in_list(
    client: httpx.Client,
    headers: dict,
    list_id: str,
    row: Milestone,
    *,
    item_kind: str = "task",
) -> str:
    prefix = ""
    if item_kind == "milestone":
        prefix = "◆ Milestone · "
    payload: dict = {
        "name": f"{prefix}{row.name[:240]}",
        "description": f"PDC {item_kind} · bobot {row.weight_pct}%",
        "tags": [item_kind],
    }
    due = _ms_timestamp(row.target_date)
    start = _ms_timestamp(row.start_date)
    if due:
        payload["due_date"] = due
    if start:
        payload["start_date"] = start
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    tid = str(resp.json().get("id", ""))
    if not tid:
        raise ClickUpSyncError(f"Gagal membuat task ClickUp: {row.name}")
    return tid


def _create_subtask(
    client: httpx.Client, headers: dict, parent_task_id: str, row: Milestone
) -> str:
    payload: dict = {"name": row.name[:255]}
    due = _ms_timestamp(row.target_date)
    start = _ms_timestamp(row.start_date)
    if due:
        payload["due_date"] = due
    if start:
        payload["start_date"] = start
    url = f"https://api.clickup.com/api/v2/task/{parent_task_id}/subtask"
    resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    tid = str(resp.json().get("id", ""))
    if not tid:
        raise ClickUpSyncError(f"Gagal membuat subtask: {row.name}")
    return tid


def provision_folder_structure(
    db: Session,
    project: Project,
    *,
    force: bool = False,
    auto_create_lists: bool = True,
) -> dict:
    if not project.kickoff_timeline_confirmed_at:
        raise ValueError("Timeline kick off belum dikonfirmasi")
    if not is_configured(db):
        raise ValueError("ClickUp belum dikonfigurasi di integrasi")
    if not project.clickup_enabled:
        raise ValueError("Aktifkan ClickUp untuk proyek ini")

    if project.clickup_provision_status == "provisioned" and not force:
        return {"created_lists": 0, "created_tasks": 0, "skipped": True}

    row = get_integration(db)
    if not row or not row.clickup_api_token_enc:
        raise ValueError("Token ClickUp tidak ada")

    if project.clickup_folder_id:
        folder_id = project.clickup_folder_id.strip()
        assert_clickup_folder_access(row, folder_id)
    else:
        folder_id = ensure_project_clickup_folder(db, project)
    phases = parent_phases_for_clickup(db, project.id)
    if not phases:
        raise ValueError("Tidak ada phase timeline kick off")

    headers = _headers(row)
    lists_created = 0
    lists_reused = 0
    tasks_created = 0
    milestones_created = 0
    subtasks_created = 0
    skipped_no_list = 0
    all_rows = kickoff_milestones(db, project.id)

    with httpx.Client(timeout=90.0) as client:
        lists_by_name = _fetch_folder_lists_map(client, headers, folder_id)
        for phase in phases:
            if not phase.clickup_list_id:
                if auto_create_lists:
                    lid, created = _ensure_list_in_folder(
                        client, headers, folder_id, phase.name, lists_by_name
                    )
                    phase.clickup_list_id = lid
                    if created:
                        lists_created += 1
                    else:
                        lists_reused += 1
                else:
                    skipped_no_list += 1
                    continue
            elif phase.clickup_list_id:
                key = _list_name_key(phase.name)
                if key:
                    lists_by_name[key] = phase.clickup_list_id

            list_id = phase.clickup_list_id
            phase.clickup_task_id = None

            for child in _phase_children(all_rows, phase.id):
                if child.item_type == TimelineItemType.milestone:
                    if not child.clickup_task_id or force:
                        due = child.target_date or child.start_date
                        if due:
                            child.start_date = child.start_date or due
                            child.target_date = child.target_date or due
                        child.clickup_task_id = _create_task_in_list(
                            client,
                            headers,
                            list_id,
                            child,
                            item_kind="milestone",
                        )
                        milestones_created += 1
                    continue

                if child.item_type == TimelineItemType.subtask:
                    continue

                task = child
                if not task.clickup_task_id or force:
                    kind = (
                        "subtask"
                        if task.item_type == TimelineItemType.subtask
                        else "task"
                    )
                    task.clickup_task_id = _create_task_in_list(
                        client, headers, list_id, task, item_kind=kind
                    )
                    tasks_created += 1

                for sub in _task_subtasks(all_rows, task.id):
                    if sub.clickup_task_id and not force:
                        continue
                    if not task.clickup_task_id:
                        continue
                    sub.clickup_task_id = _create_subtask(
                        client, headers, task.clickup_task_id, sub
                    )
                    subtasks_created += 1

    if skipped_no_list == 0 or tasks_created > 0 or subtasks_created > 0 or lists_created > 0:
        project.clickup_provision_status = "provisioned"
    project.clickup_synced_at = now_jakarta()
    db.flush()
    return {
        "created_lists": lists_created,
        "reused_lists": lists_reused,
        "created_tasks": tasks_created,
        "created_milestones": milestones_created,
        "created_subtasks": subtasks_created,
        "skipped_milestones_no_list": skipped_no_list,
        "skipped": False,
        "folder_id": folder_id,
    }
