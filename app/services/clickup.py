"""ClickUp API v2 sync."""

from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ClickUpTaskCache,
    IntegrationSettings,
    Milestone,
    Project,
    TimelineItemType,
)


class ClickUpSyncError(ValueError):
    """Sync skipped or failed — surface message to client."""


def get_integration(db: Session) -> IntegrationSettings | None:
    return db.scalar(select(IntegrationSettings).limit(1))


def is_configured(db: Session) -> bool:
    row = get_integration(db)
    return bool(row and row.clickup_api_token_enc and row.clickup_team_id)


def _normalize_clickup_token(raw: str | None) -> str:
    t = (raw or "").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t


def _headers(row: IntegrationSettings) -> dict[str, str]:
    return {
        "Authorization": _normalize_clickup_token(row.clickup_api_token_enc),
        "Content-Type": "application/json",
    }


def clickup_http_exception(e: httpx.HTTPError) -> "HTTPException":
    """Map ClickUp httpx errors to FastAPI responses with actionable detail."""
    from fastapi import HTTPException

    if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
        code = e.response.status_code
        url = str(e.request.url) if e.request else ""
        if code == 401:
            # Jangan pakai HTTP 401 — frontend menganggap sesi login aplikasi habis.
            return HTTPException(
                status_code=403,
                detail=(
                    "ClickUp: token ditolak (401 dari API ClickUp). "
                    "Buat Personal API Token baru di ClickUp → Settings → Apps, "
                    "simpan di menu Integrasi ClickUp (Settings), lalu Test connection. "
                    "Format: pk_… tanpa «Bearer»."
                ),
            )
        if code == 403:
            return HTTPException(
                status_code=403,
                detail="Token ClickUp tidak punya akses ke workspace/folder/list ini.",
            )
        if code == 404:
            return HTTPException(
                status_code=404,
                detail=(
                    "Resource ClickUp tidak ditemukan (404). "
                    "Cek Team/Space/Folder ID proyek atau buat folder proyek ulang."
                ),
            )
        if code == 429:
            return HTTPException(
                status_code=429,
                detail="Rate limit ClickUp — coba lagi beberapa menit.",
            )
        if code == 400:
            snippet = (e.response.text or "")[:300]
            if "name taken" in snippet.lower() or "SUBCAT_016" in snippet:
                return HTTPException(
                    status_code=409,
                    detail=(
                        "ClickUp: nama list sudah ada di folder ini. "
                        "Jalankan provision lagi (sistem akan pakai list yang ada) atau rename phase."
                    ),
                )
            return HTTPException(
                status_code=400,
                detail=f"ClickUp menolak permintaan (400): {snippet or e}",
            )
        snippet = (e.response.text or "")[:200]
        return HTTPException(
            status_code=502,
            detail=f"ClickUp API HTTP {code} ({url}): {snippet or e}",
        )
    return HTTPException(status_code=502, detail=f"ClickUp API error: {e}")


def verify_stored_clickup_token(db: Session) -> dict:
    """Validasi token + team ID tersimpan (sama seperti Test connection)."""
    row = get_integration(db)
    if not row or not row.clickup_api_token_enc or not row.clickup_team_id:
        raise ClickUpSyncError("Integrasi ClickUp belum lengkap (token + Team ID).")
    token = _normalize_clickup_token(row.clickup_api_token_enc)
    team_id = str(row.clickup_team_id).strip()
    if not token:
        raise ClickUpSyncError("Token ClickUp kosong — simpan di Integrasi.")
    if not team_id.isdigit():
        raise ClickUpSyncError("Team ID harus angka (dari URL ClickUp).")
    url = f"https://api.clickup.com/api/v2/team/{team_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers={"Authorization": token})
        r.raise_for_status()
        team = r.json().get("team") or {}
    return {"team_id": str(team.get("id") or team_id), "team_name": team.get("name")}


def list_clickup_spaces(db: Session) -> list[dict]:
    row = get_integration(db)
    if not row or not is_configured(db):
        raise ClickUpSyncError("Integrasi ClickUp org belum lengkap (token + team ID).")
    url = f"https://api.clickup.com/api/v2/team/{row.clickup_team_id}/space"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(row))
        r.raise_for_status()
        spaces = r.json().get("spaces") or []
    return [{"id": str(s["id"]), "name": s.get("name", "")} for s in spaces]


def list_clickup_lists_in_space(db: Session, space_id: str) -> list[dict]:
    row = get_integration(db)
    if not row or not row.clickup_api_token_enc:
        raise ClickUpSyncError("Token ClickUp tidak ada.")
    out: list[dict] = []
    with httpx.Client(timeout=45.0) as client:
        h = _headers(row)
        r = client.get(f"https://api.clickup.com/api/v2/space/{space_id}/list", headers=h)
        r.raise_for_status()
        for lst in r.json().get("lists") or []:
            out.append(
                {
                    "id": str(lst["id"]),
                    "name": lst.get("name", ""),
                    "folder_name": None,
                }
            )
        r = client.get(f"https://api.clickup.com/api/v2/space/{space_id}/folder", headers=h)
        r.raise_for_status()
        for folder in r.json().get("folders") or []:
            fid = folder.get("id")
            fname = folder.get("name", "")
            r2 = client.get(f"https://api.clickup.com/api/v2/folder/{fid}/list", headers=h)
            r2.raise_for_status()
            for lst in r2.json().get("lists") or []:
                out.append(
                    {
                        "id": str(lst["id"]),
                        "name": lst.get("name", ""),
                        "folder_name": fname,
                    }
                )
    return out


def _resolve_space_id(db: Session, project: Project, row: IntegrationSettings) -> str:
    if project.clickup_space_id:
        return project.clickup_space_id
    if row.clickup_default_space_id:
        return row.clickup_default_space_id
    spaces = list_clickup_spaces(db)
    if not spaces:
        raise ClickUpSyncError("Workspace ClickUp tidak punya Space. Buat Space di ClickUp dulu.")
    return spaces[0]["id"]


def list_clickup_lists_in_folder(db: Session, folder_id: str) -> list[dict]:
    row = get_integration(db)
    if not row or not row.clickup_api_token_enc:
        raise ClickUpSyncError("Token ClickUp tidak ada.")
    fid = folder_id.strip()
    if not fid:
        raise ClickUpSyncError("Folder ID kosong.")
    url = f"https://api.clickup.com/api/v2/folder/{fid}/list"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(row))
        r.raise_for_status()
        out: list[dict] = []
        for x in r.json().get("lists") or []:
            if x.get("id"):
                out.append({"id": str(x["id"]), "name": str(x.get("name") or "")})
        return out


def list_ids_in_folder(db: Session, folder_id: str) -> list[str]:
    return [x["id"] for x in list_clickup_lists_in_folder(db, folder_id)]


def checklist_completion_pct(task_json: dict) -> float | None:
    checklists = task_json.get("checklists") or []
    if not checklists:
        return None
    total = 0
    done = 0
    for cl in checklists:
        for item in cl.get("items") or []:
            total += 1
            if item.get("resolved"):
                done += 1
    if total == 0:
        return None
    return round(done / total * 100.0, 2)


def resolve_task_percent_complete(task_json: dict) -> float | None:
    pct = checklist_completion_pct(task_json)
    if pct is not None:
        return pct
    native = task_json.get("percent_complete")
    if native is not None:
        try:
            p = float(native)
            if p >= 0:
                return min(100.0, p)
        except (TypeError, ValueError):
            pass
    spent = task_json.get("time_spent")
    estimate = task_json.get("time_estimate")
    if estimate and spent:
        try:
            return round(min(100.0, float(spent) / float(estimate) * 100.0), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    status_obj = task_json.get("status") or {}
    status = (status_obj.get("status") or "").lower()
    if status_obj.get("type") == "closed" or status in ("complete", "closed", "done"):
        return 100.0
    from app.services.progress import _status_implies_in_progress

    if _status_implies_in_progress(status_obj.get("status")):
        return 50.0
    return None


def ensure_project_clickup_list(db: Session, project: Project) -> str:
    """Create or return ClickUp list for this project."""
    if project.clickup_list_id:
        return project.clickup_list_id
    if not project.clickup_enabled:
        raise ClickUpSyncError("Aktifkan ClickUp di proyek sebelum membuat list.")
    row = get_integration(db)
    if not row or not is_configured(db):
        raise ClickUpSyncError("Integrasi ClickUp org belum lengkap.")

    space_id = _resolve_space_id(db, project, row)
    folder_id = project.clickup_folder_id or row.clickup_default_folder_id
    list_name = f"{project.code} — {project.name}"[:255]

    if folder_id:
        url = f"https://api.clickup.com/api/v2/folder/{folder_id}/list"
    else:
        url = f"https://api.clickup.com/api/v2/space/{space_id}/list"

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=_headers(row), json={"name": list_name})
        resp.raise_for_status()
        data = resp.json()

    list_id = str(data.get("id", ""))
    if not list_id:
        raise ClickUpSyncError("ClickUp tidak mengembalikan list ID.")

    project.clickup_list_id = list_id
    project.clickup_list_name = list_name
    project.clickup_space_id = space_id
    if folder_id:
        project.clickup_folder_id = folder_id
    db.flush()
    return list_id


def _parse_date(value: int | float | str | None) -> date | None:
    """ClickUp may return due_date as ms timestamp (int) or string."""
    if value is None or value == "":
        return None
    ms: float | None = None
    if isinstance(value, (int, float)):
        ms = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            ms = float(raw)
        except ValueError:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                return None
    else:
        return None
    if ms <= 0:
        return None
    # Seconds vs milliseconds (ClickUp list tasks use ms as string)
    if ms < 1e11:
        ms *= 1000.0
    return datetime.utcfromtimestamp(ms / 1000.0).date()


def _task_closed(t: dict) -> bool:
    status_obj = t.get("status") or {}
    status = (status_obj.get("status") or "").lower()
    return status_obj.get("type") == "closed" or status in ("complete", "closed", "done")


def _cache_start_date(cache: ClickUpTaskCache) -> date | None:
    raw = cache.raw_json if isinstance(cache.raw_json, dict) else {}
    return _parse_date(raw.get("start_date"))


def _date_to_clickup_raw(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time()).timestamp() * 1000)


def _parent_date_span(
    parent: ClickUpTaskCache,
    *,
    ms_by_id: dict[int, Milestone],
    ms_by_clickup: dict[str, Milestone],
) -> tuple[date | None, date | None, dict | None]:
    """Timeline milestone dates first, then ClickUp cache (for subtask inherit on sync)."""
    m = ms_by_clickup.get(parent.clickup_task_id or "")
    if not m and parent.milestone_id:
        m = ms_by_id.get(parent.milestone_id)
    if m and (m.start_date or m.target_date):
        return m.start_date, m.target_date, None
    p_start = _cache_start_date(parent)
    p_due = parent.due_date
    praw = parent.raw_json if isinstance(parent.raw_json, dict) else {}
    if p_start or p_due:
        return p_start, p_due, praw
    return None, None, None


def _write_cache_timeline_dates(
    cache: ClickUpTaskCache,
    start: date | None,
    end: date | None,
) -> bool:
    if not start and not end:
        return False
    raw = dict(cache.raw_json) if isinstance(cache.raw_json, dict) else {}
    changed = False
    if end and cache.due_date != end:
        cache.due_date = end
        raw["due_date"] = _date_to_clickup_raw(end)
        changed = True
    if start:
        cur_start = _cache_start_date(cache)
        if cur_start != start:
            raw["start_date"] = _date_to_clickup_raw(start)
            changed = True
    if changed:
        raw["pdc_timeline_dates"] = True
        cache.raw_json = raw
    return changed


def _push_clickup_task_dates(
    client: httpx.Client,
    headers: dict[str, str],
    task_id: str,
    start: date | None,
    end: date | None,
) -> bool:
    if not task_id or (not start and not end):
        return False
    body: dict[str, int] = {}
    if end:
        body["due_date"] = _date_to_clickup_raw(end)
    if start:
        body["start_date"] = _date_to_clickup_raw(start)
    resp = client.put(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers=headers,
        json=body,
    )
    resp.raise_for_status()
    return True


def apply_timeline_dates_to_clickup(
    db: Session,
    project_id: int,
    *,
    client: httpx.Client,
    headers: dict[str, str],
) -> tuple[int, int]:
    """
    Push PDC timeline start/target to ClickUp task dates (cache + API).
    Timeline is source of truth on progress sync.
    """
    from app.services.progress import (
        _phase_id_for_cache,
        build_clickup_lookups,
        timeline_phases,
    )

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    if not milestones or not caches:
        return 0, 0

    tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    by_id = {m.id: m for m in milestones}
    ms_by_clickup = {m.clickup_task_id: m for m in milestones if m.clickup_task_id}
    phases = timeline_phases(milestones)

    def milestone_for_cache(cache: ClickUpTaskCache) -> Milestone | None:
        m = ms_by_clickup.get(cache.clickup_task_id or "")
        if m:
            return m
        for mid, linked in milestone_cache.items():
            if linked.clickup_task_id == cache.clickup_task_id:
                return by_id.get(mid)
        return None

    def timeline_span(cache: ClickUpTaskCache) -> tuple[date | None, date | None]:
        m = milestone_for_cache(cache)
        if m and (m.start_date or m.target_date):
            return m.start_date, m.target_date
        if cache.parent_task_id:
            parent = by_tid.get(cache.parent_task_id)
            if parent:
                ps, pe = timeline_span(parent)
                if ps or pe:
                    return ps, pe
        phase_id = _phase_id_for_cache(cache, phases, by_id, by_tid)
        if phase_id:
            ph = by_id.get(phase_id)
            if ph and (ph.start_date or ph.target_date):
                return ph.start_date, ph.target_date
        return None, None

    cache_updates = 0
    api_updates = 0
    seen: set[str] = set()

    for m in milestones:
        tid = m.clickup_task_id
        if not tid or tid in seen:
            continue
        cache = tasks_by_id.get(tid)
        if not cache:
            continue
        start, end = m.start_date, m.target_date
        if not start and not end:
            continue
        seen.add(tid)
        if _write_cache_timeline_dates(cache, start, end):
            cache_updates += 1
        try:
            if _push_clickup_task_dates(client, headers, tid, start, end):
                api_updates += 1
        except httpx.HTTPError:
            pass

    for cache in caches:
        tid = cache.clickup_task_id or ""
        if not tid or tid in seen:
            continue
        start, end = timeline_span(cache)
        if not start and not end:
            continue
        seen.add(tid)
        if _write_cache_timeline_dates(cache, start, end):
            cache_updates += 1
        try:
            if _push_clickup_task_dates(client, headers, tid, start, end):
                api_updates += 1
        except httpx.HTTPError:
            pass

    return cache_updates, api_updates


def realign_cache_phase_ids(db: Session, project_id: int) -> int:
    """Set cache.milestone_id to owning phase for correct timeline grouping."""
    from app.services.progress import _phase_id_for_cache

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    from app.services.progress import timeline_phases

    phases = timeline_phases(milestones)
    by_id = {m.id: m for m in milestones}
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    updated = 0
    for c in caches:
        phase_id = _phase_id_for_cache(c, phases, by_id, by_tid)
        if phase_id is not None and c.milestone_id != phase_id:
            c.milestone_id = phase_id
            updated += 1
    return updated


def inherit_empty_task_dates_from_parent(
    db: Session,
    project_id: int,
    milestones: list[Milestone],
) -> int:
    """
    Subtasks use the parent task date span (mulai/selesai = parent).
    Parent dates come from ClickUp; if empty, from linked PDC milestone baseline.
    """
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    if not caches:
        return 0
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    ms_by_id = {m.id: m for m in milestones}
    ms_by_clickup = {m.clickup_task_id: m for m in milestones if m.clickup_task_id}

    def depth(cache: ClickUpTaskCache) -> int:
        d = 0
        cur = cache
        seen: set[str] = set()
        while cur.parent_task_id and cur.parent_task_id not in seen:
            seen.add(cur.parent_task_id)
            parent = by_tid.get(cur.parent_task_id)
            if not parent:
                break
            d += 1
            cur = parent
        return d

    updated = 0
    for cache in sorted(caches, key=depth):
        if not cache.parent_task_id:
            continue
        parent = by_tid.get(cache.parent_task_id)
        if not parent:
            continue
        p_start, p_due, praw = _parent_date_span(
            parent, ms_by_id=ms_by_id, ms_by_clickup=ms_by_clickup
        )
        if not p_start and not p_due:
            continue
        raw = dict(cache.raw_json) if isinstance(cache.raw_json, dict) else {}
        if p_due:
            cache.due_date = p_due
            if praw and praw.get("due_date") not in (None, ""):
                raw["due_date"] = praw["due_date"]
            else:
                raw["due_date"] = _date_to_clickup_raw(p_due)
        if p_start:
            if praw and praw.get("start_date") not in (None, ""):
                raw["start_date"] = praw["start_date"]
            else:
                raw["start_date"] = _date_to_clickup_raw(p_start)
        raw["pdc_inherited_dates_from"] = parent.clickup_task_id
        cache.raw_json = raw
        updated += 1
    return updated


def _milestone_tree_depth(m: Milestone, by_id: dict[int, Milestone]) -> int:
    depth = 0
    cur: Milestone | None = m
    seen: set[int] = set()
    while cur and cur.parent_id and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        depth += 1
        cur = by_id.get(cur.parent_id)
    return depth


def _apply_date_span_to_milestone(
    m: Milestone,
    start: date | None,
    end: date | None,
    db: Session,
) -> bool:
    from app.services.business_calendar import count_business_days_inclusive

    changed = False
    if start and m.start_date != start:
        m.start_date = start
        changed = True
    if end and m.target_date != end:
        m.target_date = end
        changed = True
    if start and end:
        dur = count_business_days_inclusive(start, end, db)
        if m.duration_days != dur:
            m.duration_days = dur
            changed = True
    return changed


def apply_clickup_dates_to_timeline(db: Session, project_id: int) -> int:
    """
    Persist start/target milestone delivery dari tanggal ClickUp (setelah inherit subtask).
    Phase/task di-roll up dari anak; planned start/end proyek disesuaikan.
    """
    from app.services.progress import build_clickup_lookups

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    if not milestones:
        return 0
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    by_id = {m.id: m for m in milestones}
    ms_by_id = by_id
    ms_by_clickup = {m.clickup_task_id: m for m in milestones if m.clickup_task_id}
    _, milestone_cache = build_clickup_lookups(milestones, caches)
    children_map: dict[int, list[Milestone]] = {}
    for m in milestones:
        if m.parent_id:
            children_map.setdefault(m.parent_id, []).append(m)

    updated = 0
    for m in sorted(milestones, key=lambda x: (_milestone_tree_depth(x, by_id), x.id), reverse=True):
        cache = by_tid.get(m.clickup_task_id) if m.clickup_task_id else milestone_cache.get(m.id)
        applied = False
        if cache:
            start: date | None = None
            end: date | None = None
            if m.item_type == TimelineItemType.subtask and cache.parent_task_id:
                parent = by_tid.get(cache.parent_task_id)
                if parent:
                    start, end, _ = _parent_date_span(
                        parent, ms_by_id=ms_by_id, ms_by_clickup=ms_by_clickup
                    )
            if not start and not end:
                start = _cache_start_date(cache)
                end = cache.due_date
            if start or end:
                applied = _apply_date_span_to_milestone(
                    m,
                    start or m.start_date,
                    end or m.target_date,
                    db,
                )
        if not applied and m.item_type in (
            TimelineItemType.phase,
            TimelineItemType.task,
        ):
            kids = children_map.get(m.id, [])
            starts = [k.start_date for k in kids if k.start_date]
            ends = [k.target_date for k in kids if k.target_date]
            if starts and ends:
                applied = _apply_date_span_to_milestone(
                    m, min(starts), max(ends), db
                )
        if applied:
            updated += 1

    project = db.get(Project, project_id)
    if project:
        work = [
            m
            for m in milestones
            if m.item_type
            in (TimelineItemType.phase, TimelineItemType.task, TimelineItemType.subtask)
        ]
        starts = [m.start_date for m in work if m.start_date]
        targets = [m.target_date for m in work if m.target_date]
        if starts:
            project.planned_start_date = min(starts)
        if targets:
            project.planned_end_date = max(targets)

    return updated


def _upsert_task_cache(
    db: Session,
    project: Project,
    t: dict,
    *,
    list_id: str | None = None,
    milestone_id: int | None = None,
) -> None:
    tid = str(t.get("id", ""))
    if not tid:
        return
    status_obj = t.get("status") or {}
    status = status_obj.get("status", "")
    existing = db.scalar(
        select(ClickUpTaskCache).where(
            ClickUpTaskCache.project_id == project.id,
            ClickUpTaskCache.clickup_task_id == tid,
        )
    )
    pct = resolve_task_percent_complete(t)
    row_data = {
        "name": t.get("name", ""),
        "status": status,
        "assignees": t.get("assignees") or [],
        "due_date": _parse_date(t.get("due_date")),
        "priority": (t.get("priority") or {}).get("priority") if t.get("priority") else None,
        "url": t.get("url"),
        "time_spent_ms": t.get("time_spent"),
        "time_estimate_ms": t.get("time_estimate"),
        "percent_complete": pct,
        "is_closed": _task_closed(t),
        "parent_task_id": str(t.get("parent") or "") or None,
        "clickup_list_id": list_id,
        "milestone_id": milestone_id,
        "raw_json": t,
        "synced_at": datetime.utcnow(),
    }
    if existing:
        for k, v in row_data.items():
            setattr(existing, k, v)
    else:
        db.add(ClickUpTaskCache(project_id=project.id, clickup_task_id=tid, **row_data))


def _sync_list_tasks(
    db: Session,
    project: Project,
    row: IntegrationSettings,
    list_id: str,
    milestone_id: int | None,
    client: httpx.Client,
) -> tuple[int, set[str]]:
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    params = {"include_closed": "true", "subtasks": "true"}
    resp = client.get(url, headers=_headers(row), params=params)
    resp.raise_for_status()
    tasks = resp.json().get("tasks") or []
    count = 0
    seen: set[str] = set()
    for t in tasks:
        tid = str(t.get("id", ""))
        if tid:
            seen.add(tid)
        _upsert_task_cache(db, project, t, list_id=list_id, milestone_id=milestone_id)
        count += 1
    return count, seen


def _fetch_task_or_dead(
    client: httpx.Client,
    headers: dict[str, str],
    task_id: str,
) -> tuple[dict | None, bool]:
    """Return (task_json, is_dead) where is_dead means ClickUp returned 404."""
    detail_url = f"https://api.clickup.com/api/v2/task/{task_id}"
    dr = client.get(detail_url, headers=headers)
    if dr.status_code == 404:
        return None, True
    dr.raise_for_status()
    return dr.json(), False


def sync_project_tasks(db: Session, project: Project) -> dict:
    from app.services.progress import clickup_status_mapping_context

    if not project.clickup_enabled:
        raise ClickUpSyncError(
            "ClickUp belum diaktifkan. Centang «Aktifkan ClickUp» lalu Simpan."
        )
    row = get_integration(db)
    if not row or not row.clickup_api_token_enc:
        raise ClickUpSyncError("Token ClickUp org tidak ada. Setting → Integrasi ClickUp.")

    all_ms_for_map = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
    )
    from app.services.progress import timeline_phases

    ms_by_list: dict[str, int] = {}
    for m in timeline_phases(all_ms_for_map):
        lid = (m.clickup_list_id or "").strip()
        if lid:
            ms_by_list[lid] = m.id
    container_ids = {
        m.clickup_task_id
        for m in db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
        if m.clickup_task_id
    }

    list_ids: list[str] = []
    if project.clickup_folder_id:
        try:
            list_ids = list_ids_in_folder(db, project.clickup_folder_id)
        except httpx.HTTPError as e:
            raise ClickUpSyncError(f"Gagal baca folder ClickUp: {e}") from e
        if not list_ids:
            raise ClickUpSyncError(
                "Folder proyek kosong. Jalankan «Generate struktur» (provision) dulu."
            )
    elif project.clickup_list_id:
        list_ids = [project.clickup_list_id.strip()]
    else:
        raise ClickUpSyncError(
            "Belum ada folder/list ClickUp. Klik «Buat folder proyek» lalu «Generate struktur»."
        )

    count = 0
    removed_cache = 0
    unlinked_milestones = 0
    headers = _headers(row)
    with clickup_status_mapping_context(db):
        try:
            with httpx.Client(timeout=90.0) as client:
                seen_clickup_ids: set[str] = set()
                dead_ids: set[str] = set()
                for lid in list_ids:
                    mid = ms_by_list.get(lid)
                    n, seen = _sync_list_tasks(db, project, row, lid, mid, client)
                    count += n
                    seen_clickup_ids |= seen
                all_ms = list(
                    db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
                )
                for cid in container_ids:
                    if not cid:
                        continue
                    t, is_dead = _fetch_task_or_dead(client, headers, cid)
                    if is_dead:
                        dead_ids.add(cid)
                        continue
                    if t:
                        tid = str(t.get("id", cid))
                        seen_clickup_ids.add(tid)
                        owner = next((m for m in all_ms if m.clickup_task_id == cid), None)
                        _upsert_task_cache(
                            db,
                            project,
                            t,
                            list_id=owner.clickup_list_id if owner else None,
                            milestone_id=owner.id if owner else None,
                        )

                caches = list(
                    db.scalars(
                        select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project.id)
                    ).all()
                )
                for cache in caches:
                    tid = cache.clickup_task_id
                    if not tid or tid in seen_clickup_ids:
                        continue
                    if tid in dead_ids:
                        db.delete(cache)
                        removed_cache += 1
                        continue
                    t, is_dead = _fetch_task_or_dead(client, headers, tid)
                    if is_dead:
                        db.delete(cache)
                        removed_cache += 1
                    elif t:
                        seen_clickup_ids.add(tid)
                        _upsert_task_cache(db, project, t, list_id=cache.clickup_list_id)

                for m in all_ms:
                    cid = m.clickup_task_id
                    if not cid or cid in seen_clickup_ids:
                        continue
                    if cid in dead_ids:
                        m.clickup_task_id = None
                        unlinked_milestones += 1
                        continue
                    _, is_dead = _fetch_task_or_dead(client, headers, cid)
                    if is_dead:
                        m.clickup_task_id = None
                        unlinked_milestones += 1
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response is not None else str(e)
            code = e.response.status_code if e.response is not None else "?"
            raise ClickUpSyncError(f"ClickUp API ({code}): {body}") from e
        except httpx.HTTPError as e:
            raise ClickUpSyncError(f"Gagal hubung ke ClickUp: {e}") from e

        from app.services.progress import relink_milestone_clickup_ids

        all_ms = list(
            db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
        )
        realigned_phases = realign_cache_phase_ids(db, project.id)
        relinked = relink_milestone_clickup_ids(db, project.id)
        all_ms = list(
            db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
        )
        clickup_dates_from_timeline = 0
        clickup_api_date_updates = 0
        with httpx.Client(timeout=90.0) as date_client:
            clickup_dates_from_timeline, clickup_api_date_updates = apply_timeline_dates_to_clickup(
                db,
                project.id,
                client=date_client,
                headers=headers,
            )
        inherited_dates = inherit_empty_task_dates_from_parent(db, project.id, all_ms)

        project.clickup_synced_at = datetime.utcnow()
        db.flush()
        return {
            "synced": count,
            "removed_cache": removed_cache,
            "unlinked_milestones": unlinked_milestones,
            "relinked_milestones": relinked,
            "inherited_dates": inherited_dates,
            "realigned_phases": realigned_phases,
            "clickup_dates_from_timeline": clickup_dates_from_timeline,
            "clickup_api_date_updates": clickup_api_date_updates,
            "timeline_dates_updated": 0,
        }


def _phase_for_milestone(
    m: Milestone, by_id: dict[int, Milestone], phases: list[Milestone]
) -> Milestone | None:
    cur: Milestone | None = m
    while cur:
        if cur.item_type == TimelineItemType.phase:
            return cur
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    if m.clickup_list_id:
        for p in phases:
            if p.clickup_list_id == m.clickup_list_id:
                return p
    return None


def _walk_phase_timeline_items(phase_id: int, milestones: list[Milestone]) -> list[Milestone]:
    out: list[Milestone] = []

    def walk(parent_id: int) -> None:
        kids = sorted(
            [m for m in milestones if m.parent_id == parent_id],
            key=lambda x: (x.sort_order, x.id),
        )
        for k in kids:
            if k.item_type == TimelineItemType.phase:
                continue
            out.append(k)
            if k.item_type == TimelineItemType.task:
                walk(k.id)

    walk(phase_id)
    return out


def _children_caches(
    parent_clickup_id: str, caches_by_parent: dict[str, list[ClickUpTaskCache]]
) -> list[ClickUpTaskCache]:
    return sorted(
        caches_by_parent.get(parent_clickup_id, []),
        key=lambda c: (c.name or "", c.id),
    )


def _task_recap_row(
    c: ClickUpTaskCache,
    *,
    sort_order: int,
    milestone_id: int | None,
    phase: Milestone | None,
    phase_status: str | None,
    item_type: str,
    depth: int,
    parent_clickup_task_id: str | None,
    subtasks: list[ClickUpTaskCache] | None = None,
) -> dict:
    from app.services.progress import classify_clickup_status, task_cache_progress_pct

    list_id = c.clickup_list_id or (phase.clickup_list_id if phase else None)
    raw_status = c.status
    return {
        "id": c.id,
        "clickup_task_id": c.clickup_task_id,
        "clickup_list_id": list_id,
        "parent_clickup_task_id": parent_clickup_task_id,
        "name": c.name,
        "status": classify_clickup_status(raw_status),
        "status_raw": raw_status,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "is_closed": c.is_closed,
        "time_spent_ms": c.time_spent_ms,
        "time_estimate_ms": c.time_estimate_ms,
        "url": c.url,
        "percent_complete": task_cache_progress_pct(c, subtasks=subtasks),
        "sort_order": sort_order,
        "milestone_id": milestone_id,
        "phase_name": phase.name if phase else None,
        "phase_status": phase_status,
        "item_type": item_type,
        "depth": depth,
    }


def task_recap(db: Session, project_id: int) -> dict:
    from app.models import TimelineItemType
    from app.services.progress import (
        _clickup_roots_for_phase,
        build_clickup_lookups,
        phase_workflow_status,
        row_clickup_progress_pct,
    )

    milestones = list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.parent_id.nulls_first(), Milestone.id)
        ).all()
    )
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    tasks_by_id, by_mid = build_clickup_lookups(milestones, caches)
    by_id = {m.id: m for m in milestones}
    phases = sorted(
        [m for m in milestones if m.item_type == TimelineItemType.phase],
        key=lambda p: (p.sort_order, p.id),
    )
    caches_by_parent: dict[str, list[ClickUpTaskCache]] = {}
    for c in caches:
        if c.parent_task_id:
            caches_by_parent.setdefault(c.parent_task_id, []).append(c)

    seen_cache_ids: set[int] = set()
    tasks_flat: list[dict] = []
    groups: list[dict] = []

    def emit_cache_row(
        cache: ClickUpTaskCache,
        *,
        milestone: Milestone | None,
        phase: Milestone | None,
        phase_status: str | None,
        item_type: str,
        depth: int,
        parent_tid: str | None,
        sort_order: int,
        cu_subs: list[ClickUpTaskCache] | None = None,
    ) -> None:
        if cache.id in seen_cache_ids:
            return
        seen_cache_ids.add(cache.id)
        row = _task_recap_row(
            cache,
            sort_order=sort_order,
            milestone_id=milestone.id if milestone else cache.milestone_id,
            phase=phase,
            phase_status=phase_status,
            item_type=item_type,
            depth=depth,
            parent_clickup_task_id=parent_tid,
            subtasks=cu_subs,
        )
        tasks_flat.append(row)

    def emit_clickup_subtree(
        cache: ClickUpTaskCache,
        *,
        phase: Milestone | None,
        phase_status: str | None,
        milestone: Milestone | None,
        item_type: str,
        depth: int,
        parent_tid: str | None,
        sort_order: int,
    ) -> None:
        cu_subs = _children_caches(cache.clickup_task_id, caches_by_parent)
        emit_cache_row(
            cache,
            milestone=milestone,
            phase=phase,
            phase_status=phase_status,
            item_type=item_type,
            depth=depth,
            parent_tid=parent_tid,
            sort_order=sort_order,
            cu_subs=cu_subs or None,
        )
        for i, sub in enumerate(cu_subs):
            if sub.id in seen_cache_ids:
                continue
            emit_clickup_subtree(
                sub,
                phase=phase,
                phase_status=phase_status,
                milestone=None,
                item_type="subtask",
                depth=depth + 1,
                parent_tid=cache.clickup_task_id,
                sort_order=sort_order + i + 1,
            )

    for phase in phases:
        phase_status = phase_workflow_status(
            phase, milestones, tasks_by_id, by_mid, caches=caches
        )
        group_tasks: list[dict] = []
        for m in _walk_phase_timeline_items(phase.id, milestones):
            cache = by_mid.get(m.id)
            if not cache:
                continue
            before = len(tasks_flat)
            emit_clickup_subtree(
                cache,
                phase=phase,
                phase_status=phase_status,
                milestone=m,
                item_type=m.item_type.value,
                depth=0 if m.item_type != TimelineItemType.subtask else 1,
                parent_tid=None,
                sort_order=m.sort_order,
            )
            group_tasks.extend(tasks_flat[before:])

        linked_cu = {m.clickup_task_id for m in milestones if m.clickup_task_id}
        extra_roots = [
            c
            for c in _clickup_roots_for_phase(phase, milestones, caches, linked_cu)
            if c.id not in seen_cache_ids
        ]
        for i, c in enumerate(extra_roots):
            before = len(tasks_flat)
            emit_clickup_subtree(
                c,
                phase=phase,
                phase_status=phase_status,
                milestone=None,
                item_type="task",
                depth=0,
                parent_tid=None,
                sort_order=900_000 + i,
            )
            group_tasks.extend(tasks_flat[before:])

        if group_tasks:
            phase_progress = row_clickup_progress_pct(
                phase, milestones, tasks_by_id, by_mid, caches=caches
            )
            groups.append(
                {
                    "list_id": phase.clickup_list_id or "",
                    "list_name": phase.name,
                    "sort_order": phase.sort_order,
                    "phase_status": phase_status,
                    "phase_progress_pct": phase_progress,
                    "tasks": group_tasks,
                }
            )

    list_to_phase = {p.clickup_list_id: p for p in phases if p.clickup_list_id}
    group_by_list = {g["list_id"]: g for g in groups if g.get("list_id")}

    remaining = [c for c in caches if c.id not in seen_cache_ids]
    for i, c in enumerate(remaining):
        if c.parent_task_id and c.parent_task_id in {
            x.clickup_task_id for x in caches if x.id in seen_cache_ids
        }:
            continue
        phase = list_to_phase.get(c.clickup_list_id or "")
        phase_status = (
            phase_workflow_status(phase, milestones, tasks_by_id, by_mid, caches=caches)
            if phase
            else None
        )
        before = len(tasks_flat)
        emit_clickup_subtree(
            c,
            phase=phase,
            phase_status=phase_status,
            milestone=None,
            item_type="subtask" if c.parent_task_id else "task",
            depth=1 if c.parent_task_id else 0,
            parent_tid=c.parent_task_id,
            sort_order=999_000 + i,
        )
        new_rows = tasks_flat[before:]
        if not new_rows:
            continue
        lid = phase.clickup_list_id if phase else (c.clickup_list_id or "")
        if phase and lid in group_by_list:
            group_by_list[lid]["tasks"].extend(new_rows)
        elif phase:
            grp = {
                "list_id": lid,
                "list_name": phase.name,
                "sort_order": phase.sort_order,
                "phase_status": phase_status,
                "tasks": new_rows,
            }
            groups.append(grp)
            group_by_list[lid] = grp
        else:
            orphan_key = ""
            if orphan_key not in group_by_list:
                grp = {
                    "list_id": "",
                    "list_name": "Lainnya",
                    "sort_order": 999_999,
                    "phase_status": None,
                    "tasks": [],
                }
                groups.append(grp)
                group_by_list[orphan_key] = grp
            group_by_list[orphan_key]["tasks"].extend(new_rows)

    groups.sort(key=lambda g: g.get("sort_order", 999_999))

    total = len(tasks_flat)
    closed = sum(1 for t in tasks_flat if t.get("is_closed"))
    overdue = sum(
        1
        for t in tasks_flat
        if not t.get("is_closed") and t.get("due_date") and t["due_date"] < date.today().isoformat()
    )
    return {
        "total": total,
        "closed": closed,
        "open": total - closed,
        "overdue": overdue,
        "tasks": tasks_flat,
        "groups": groups,
    }
