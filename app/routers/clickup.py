import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import ClickUpStructureTemplate, IntegrationSettings, Milestone, Project, User
from app.services.clickup import (
    ClickUpSyncError,
    _normalize_clickup_token,
    clickup_http_exception,
    ensure_project_clickup_list,
    is_configured,
    list_clickup_lists_in_folder,
    list_clickup_lists_in_space,
    list_clickup_spaces,
    sync_project_tasks,
    verify_stored_clickup_token,
)
from app.services.clickup_hierarchy import (
    ensure_project_clickup_folder,
    parent_milestones_for_clickup,
    parent_phases_for_clickup,
)
from app.services.clickup_provision import provision_timeline_tasks
from app.services.progress import (
    BUCKET_DEFAULT_PROGRESS,
    CLICKUP_PROGRESS_BUCKETS,
    DEFAULT_STATUS_MAPPING_TEMPLATES,
    normalize_progress_bucket,
)

router = APIRouter(prefix="/integrations/clickup", tags=["clickup"])


class ClickUpConfigPatch(BaseModel):
    clickup_api_token: str | None = None
    clickup_team_id: str | None = None
    clickup_default_space_id: str | None = None
    clickup_offer_on_kickoff: bool | None = None


class ClickUpTestBody(BaseModel):
    """Optional — test dengan nilai form tanpa harus simpan dulu."""

    clickup_api_token: str | None = None
    clickup_team_id: str | None = None


class ProjectClickUpPatch(BaseModel):
    clickup_enabled: bool | None = None
    clickup_list_id: str | None = None
    clickup_list_name: str | None = None
    clickup_space_id: str | None = None
    clickup_folder_id: str | None = None


class MilestoneListMapItem(BaseModel):
    milestone_id: int
    clickup_list_id: str | None = None


class MilestoneListMapBody(BaseModel):
    mappings: list[MilestoneListMapItem]


class ClickUpStatusMappingItem(BaseModel):
    status: str
    bucket: str
    progress_fallback: float | None = None


class ClickUpStatusMappingBody(BaseModel):
    mappings: list[ClickUpStatusMappingItem]


def _integration_row(db: Session) -> IntegrationSettings:
    row = db.scalar(select(IntegrationSettings).limit(1))
    if not row:
        row = IntegrationSettings(clickup_offer_on_kickoff=True)
        db.add(row)
        db.flush()
    return row


@router.get("/status-mappings")
def get_status_mappings(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure")),
):
    row = _integration_row(db)
    raw = row.clickup_status_mappings if isinstance(row.clickup_status_mappings, list) else []
    mappings = []
    for m in raw:
        if not isinstance(m, dict) or not m.get("status"):
            continue
        bucket = normalize_progress_bucket(str(m.get("bucket") or "")) or str(m.get("bucket", ""))
        pf = m.get("progress_fallback")
        entry: dict[str, str | float] = {
            "status": str(m.get("status", "")),
            "bucket": bucket,
        }
        if pf is not None and pf != "":
            entry["progress_fallback"] = float(pf)
        elif bucket in BUCKET_DEFAULT_PROGRESS:
            entry["progress_fallback"] = BUCKET_DEFAULT_PROGRESS[bucket]
        mappings.append(entry)
    return {
        "mappings": mappings,
        "buckets": list(CLICKUP_PROGRESS_BUCKETS),
        "bucket_default_progress": BUCKET_DEFAULT_PROGRESS,
        "default_templates": DEFAULT_STATUS_MAPPING_TEMPLATES,
        "progress_rules": (
            "Progress di timeline: (1) percent_complete / time tracking ClickUp jika ada; "
            "(2) else progress_fallback dari mapping ini; (3) else default kode."
        ),
    }


@router.put("/status-mappings")
def put_status_mappings(
    body: ClickUpStatusMappingBody,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure")),
):
    stored: list[dict[str, str | float]] = []
    seen: set[str] = set()
    for item in body.mappings:
        label = item.status.strip()
        bucket = normalize_progress_bucket(item.bucket)
        if not label or not bucket:
            raise HTTPException(
                status_code=400,
                detail=f"Mapping tidak valid: status={item.status!r} bucket={item.bucket!r}",
            )
        from app.services.progress import _norm_clickup_status

        key = _norm_clickup_status(label)
        if key in seen:
            continue
        seen.add(key)
        pf = item.progress_fallback
        if pf is None:
            pf = BUCKET_DEFAULT_PROGRESS[bucket]
        else:
            pf = max(0.0, min(100.0, float(pf)))
        stored.append({"status": label, "bucket": bucket, "progress_fallback": pf})
    row = _integration_row(db)
    row.clickup_status_mappings = stored
    db.commit()
    return {"updated": len(stored), "mappings": stored}


@router.get("")
def get_config(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure", "integrations.clickup.project")),
):
    row = db.scalar(select(IntegrationSettings).limit(1))
    if not row:
        return {"configured": False}
    return {
        "configured": is_configured(db),
        "clickup_team_id": row.clickup_team_id,
        "clickup_default_space_id": row.clickup_default_space_id,
        "clickup_offer_on_kickoff": row.clickup_offer_on_kickoff,
        "has_token": bool(row.clickup_api_token_enc),
    }


@router.patch("")
def patch_config(
    body: ClickUpConfigPatch,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure")),
):
    row = db.scalar(select(IntegrationSettings).limit(1))
    if not row:
        row = IntegrationSettings()
        db.add(row)
    if body.clickup_api_token:
        row.clickup_api_token_enc = _normalize_clickup_token(body.clickup_api_token)
    if body.clickup_team_id is not None:
        tid = body.clickup_team_id.strip()
        row.clickup_team_id = tid or None
    if body.clickup_default_space_id is not None:
        sid = body.clickup_default_space_id.strip()
        row.clickup_default_space_id = sid or None
    if body.clickup_offer_on_kickoff is not None:
        row.clickup_offer_on_kickoff = body.clickup_offer_on_kickoff
    db.commit()
    return {"updated": True}


def _resolve_clickup_credentials(
    row: IntegrationSettings | None,
    body: ClickUpTestBody | None,
) -> tuple[str | None, str | None]:
    token: str | None = None
    if body and body.clickup_api_token and body.clickup_api_token.strip():
        token = body.clickup_api_token.strip()
    elif row and row.clickup_api_token_enc:
        token = row.clickup_api_token_enc.strip()

    team_id: str | None = None
    if body and body.clickup_team_id is not None and body.clickup_team_id.strip():
        team_id = body.clickup_team_id.strip()
    elif row and row.clickup_team_id:
        team_id = row.clickup_team_id.strip()
    return token, team_id


@router.post("/test")
def test_connection(
    body: ClickUpTestBody | None = None,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure")),
):
    row = db.scalar(select(IntegrationSettings).limit(1))
    token, team_id = _resolve_clickup_credentials(row, body)
    if not token or not team_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Lengkapi API token dan Team ID (angka). "
                "Team ID ada di URL ClickUp: app.clickup.com/{team_id}/…"
            ),
        )
    if not team_id.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Team ID harus angka (bukan email atau nama).",
        )
    headers = {"Authorization": _normalize_clickup_token(token)}
    url = f"https://api.clickup.com/api/v2/team/{team_id}"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e
    team = data.get("team") or {}
    return {"ok": True, "team_name": team.get("name"), "team_id": team.get("id")}


@router.get("/spaces")
def get_spaces(
    db: Session = Depends(get_db),
    _: set[str] = Depends(
        PermissionChecker("integrations.clickup.configure", "integrations.clickup.project")
    ),
):
    try:
        return {"spaces": list_clickup_spaces(db)}
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e


@router.get("/spaces/{space_id}/lists")
def get_space_lists(
    space_id: str,
    db: Session = Depends(get_db),
    _: set[str] = Depends(
        PermissionChecker("integrations.clickup.configure", "integrations.clickup.project")
    ),
):
    try:
        return {"lists": list_clickup_lists_in_space(db, space_id)}
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e


@router.get("/structure-templates")
def list_templates(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.clickup.configure")),
):
    rows = db.scalars(select(ClickUpStructureTemplate)).all()
    return [{"id": r.id, "name": r.name, "is_default": r.is_default} for r in rows]


@router.get("/by-project/{project_id}")
def get_project_clickup(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "configured": is_configured(db),
        "clickup_enabled": project.clickup_enabled,
        "clickup_list_id": project.clickup_list_id,
        "clickup_list_name": project.clickup_list_name,
        "clickup_space_id": project.clickup_space_id,
        "clickup_folder_id": project.clickup_folder_id,
        "clickup_provision_status": project.clickup_provision_status,
        "kickoff_timeline_confirmed": bool(project.kickoff_timeline_confirmed_at),
        "sync_mode": "folder"
        if project.clickup_folder_id
        else ("legacy_list" if project.clickup_list_id else "none"),
    }


@router.patch("/by-project/{project_id}")
def patch_project_clickup(
    project_id: int,
    body: ProjectClickUpPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_write(project_id, user, codes, db)
    if not is_configured(db) and body.clickup_enabled:
        raise HTTPException(status_code=400, detail="ClickUp org setup incomplete")
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if "clickup_list_id" in data and data["clickup_list_id"] is not None:
        lid = str(data["clickup_list_id"]).strip()
        data["clickup_list_id"] = lid or None
    if "clickup_folder_id" in data and data["clickup_folder_id"] is not None:
        fid = str(data["clickup_folder_id"]).strip()
        data["clickup_folder_id"] = fid or None
    for k, v in data.items():
        setattr(project, k, v)
    db.commit()
    return {"clickup_enabled": project.clickup_enabled}


@router.get("/by-project/{project_id}/folder-lists")
def get_folder_lists_for_project(
    project_id: int,
    folder_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    fid = (folder_id or project.clickup_folder_id or "").strip()
    if not fid:
        raise HTTPException(
            status_code=400,
            detail="Isi Folder ID (existing) lalu simpan, atau buat folder baru.",
        )
    try:
        return {"folder_id": fid, "lists": list_clickup_lists_in_folder(db, fid)}
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e


def _phase_map_payload(db: Session, project_id: int, project: Project) -> dict:
    phases = parent_phases_for_clickup(db, project_id)
    rows = [
        {
            "phase_id": m.id,
            "milestone_id": m.id,
            "name": m.name,
            "clickup_list_id": m.clickup_list_id,
            "clickup_task_id": m.clickup_task_id,
        }
        for m in phases
    ]
    return {
        "kickoff_timeline_confirmed": bool(project.kickoff_timeline_confirmed_at),
        "phases": rows,
        "milestones": rows,
    }


@router.get("/by-project/{project_id}/phase-list-map")
@router.get("/by-project/{project_id}/milestone-list-map")
def get_phase_list_map(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    return _phase_map_payload(db, project_id, project)


@router.put("/by-project/{project_id}/phase-list-map")
@router.put("/by-project/{project_id}/milestone-list-map")
def put_phase_list_map(
    project_id: int,
    body: MilestoneListMapBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    allowed = {m.id for m in parent_phases_for_clickup(db, project_id)}
    updated = 0
    for item in body.mappings:
        if item.milestone_id not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Phase {item.milestone_id} bukan phase timeline kick off.",
            )
        m = db.get(Milestone, item.milestone_id)
        if not m or m.project_id != project_id:
            raise HTTPException(status_code=404, detail="Phase not found")
        lid = (item.clickup_list_id or "").strip() or None
        m.clickup_list_id = lid
        updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/by-project/{project_id}/ensure-folder")
def ensure_folder_for_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        folder_id = ensure_project_clickup_folder(db, project)
        db.commit()
        return {
            "clickup_folder_id": folder_id,
            "clickup_space_id": project.clickup_space_id,
        }
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e


@router.post("/by-project/{project_id}/ensure-list")
def ensure_list_for_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        list_id = ensure_project_clickup_list(db, project)
        db.commit()
        return {
            "clickup_list_id": list_id,
            "clickup_list_name": project.clickup_list_name,
            "clickup_space_id": project.clickup_space_id,
        }
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise clickup_http_exception(e) from e


@router.post("/by-project/{project_id}/provision-timeline")
def provision_clickup_timeline(
    project_id: int,
    force: bool = False,
    auto_create_lists: bool = Query(True, description="Buat list baru jika milestone belum punya mapping"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_write(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        try:
            verify_stored_clickup_token(db)
        except ClickUpSyncError as cred_err:
            raise HTTPException(status_code=400, detail=str(cred_err)) from cred_err
        except httpx.HTTPError as cred_http:
            raise clickup_http_exception(cred_http) from cred_http

        result = provision_timeline_tasks(
            db, project, force=force, auto_create_lists=auto_create_lists
        )
        try:
            sync_project_tasks(db, project)
        except ClickUpSyncError as sync_err:
            db.commit()
            result = {**result, "sync_warning": str(sync_err)}
            return result
        db.commit()
        return result
    except ClickUpSyncError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        db.rollback()
        raise clickup_http_exception(e) from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Provision ClickUp gagal: {e}") from e


@router.post("/by-project/{project_id}/sync")
def sync_clickup(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_project_read(project_id, user, codes, db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        result = sync_project_tasks(db, project)
        db.commit()
        return {**result, "synced_tasks": result.get("synced", 0)}
    except ClickUpSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
