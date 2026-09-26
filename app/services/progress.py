from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Milestone, MilestoneStatus, Project, TimelineItemType


def normalize_timeline_match_name(name: str) -> str:
    n = (name or "").strip().lower()
    prefixes = (
        "◆ milestone · ",
        "◆ milestone - ",
        "◆ milestone ",
        "milestone · ",
        "milestone - ",
    )
    for p in prefixes:
        if n.startswith(p):
            n = n[len(p) :].strip()
            break
    return n


def build_clickup_lookups(
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
) -> tuple[dict[str, ClickUpTaskCache], dict[int, ClickUpTaskCache]]:
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    by_name: dict[str, ClickUpTaskCache] = {}
    for c in caches:
        by_name[normalize_timeline_match_name(c.name)] = c
    by_milestone_id: dict[int, ClickUpTaskCache] = {}
    for m in milestones:
        cache = by_tid.get(m.clickup_task_id) if m.clickup_task_id else None
        if not cache:
            cache = by_name.get(normalize_timeline_match_name(m.name))
        if cache:
            by_milestone_id[m.id] = cache
    return by_tid, by_milestone_id


def relink_milestone_clickup_ids(db: Session, project_id: int) -> int:
    """Set milestone.clickup_task_id from cache name match when missing."""
    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    caches = list(
        db.scalars(select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)).all()
    )
    by_id = {m.id: m for m in milestones}
    phases = [m for m in milestones if m.item_type == TimelineItemType.phase]
    _, by_mid = build_clickup_lookups(milestones, caches)
    linked = 0

    def phase_list_for(milestone: Milestone) -> str | None:
        cur: Milestone | None = milestone
        while cur:
            if cur.item_type == TimelineItemType.phase:
                return cur.clickup_list_id
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        return None

    for m in milestones:
        cache = by_mid.get(m.id)
        if not cache or m.clickup_task_id:
            continue
        plist = phase_list_for(m)
        if plist and cache.clickup_list_id and cache.clickup_list_id != plist:
            continue
        m.clickup_task_id = cache.clickup_task_id
        linked += 1
    return linked


def _cache_for_milestone(
    row: Milestone,
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache],
) -> ClickUpTaskCache | None:
    if row.id in milestone_cache:
        return milestone_cache[row.id]
    tid = row.clickup_task_id or ""
    return tasks_by_id.get(tid) if tid else None


def _norm_clickup_status(status: str | None) -> str:
    return (status or "").strip().lower().replace("_", " ")


# ClickUp custom statuses → PDC workflow buckets (TODO / IN PROGRESS / DONE)
_CLICKUP_STATUS_DONE = frozenset(
    {
        "complete",
        "completed",
        "closed",
        "done",
        "ready to deploy",
        "shipped",
        "resolved",
    }
)
_CLICKUP_STATUS_IN_PROGRESS = frozenset(
    {
        "in progress",
        "on hold",
        "re open",
        "reopen",
        "ready to test",
        "internal review",
        "active",
        "doing",
        "working",
        "development",
        "review",
        "testing",
        "blocked",
        "pending",
    }
)
_CLICKUP_STATUS_TODO = frozenset(
    {
        "",
        "open",
        "to do",
        "todo",
        "not started",
        "backlog",
        "unstarted",
        "new",
    }
)


def classify_clickup_status(status: str | None) -> str:
    """Map raw ClickUp status to TODO | IN PROGRESS | DONE."""
    st = _norm_clickup_status(status)
    if st in _CLICKUP_STATUS_DONE:
        return "DONE"
    if st in _CLICKUP_STATUS_IN_PROGRESS:
        return "IN PROGRESS"
    if st in _CLICKUP_STATUS_TODO:
        return "TODO"
    if "complete" in st or "closed" in st or "deploy" in st:
        return "DONE"
    if (
        "progress" in st
        or "review" in st
        or "test" in st
        or "hold" in st
        or "re open" in st
        or st.startswith("reopen")
    ):
        return "IN PROGRESS"
    return "TODO"


def _status_implies_in_progress(status: str | None) -> bool:
    return classify_clickup_status(status) == "IN PROGRESS"


def _task_completion(
    task: ClickUpTaskCache,
    *,
    subtasks: list[ClickUpTaskCache] | None = None,
) -> float:
    if subtasks:
        vals = [_task_completion(st) for st in subtasks]
        if vals:
            return round(sum(vals) / len(vals), 2)
    if task.is_closed or (task.percent_complete or 0) >= 100:
        return 100.0
    if task.percent_complete is not None and task.percent_complete > 0:
        return float(task.percent_complete)
    if task.time_estimate_ms and task.time_estimate_ms > 0 and task.time_spent_ms:
        return round(min(100.0, task.time_spent_ms / task.time_estimate_ms * 100.0), 2)
    raw = task.raw_json if isinstance(task.raw_json, dict) else {}
    native = raw.get("percent_complete")
    if native is not None:
        try:
            p = float(native)
            if p > 0:
                return min(100.0, p)
        except (TypeError, ValueError):
            pass
    if _status_implies_in_progress(task.status):
        return 50.0
    return 0.0


def task_cache_progress_pct(
    cache: ClickUpTaskCache | None,
    *,
    subtasks: list[ClickUpTaskCache] | None = None,
) -> float | None:
    if not cache:
        return None
    return _task_completion(cache, subtasks=subtasks)


def _row_clickup_pct(
    row: Milestone,
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
) -> float:
    mc = milestone_cache or {}
    t = _cache_for_milestone(row, tasks_by_id, mc)
    return _task_completion(t) if t else 0.0


def _cache_is_done(cache: ClickUpTaskCache) -> bool:
    if cache.is_closed or (cache.percent_complete or 0) >= 100:
        return True
    return classify_clickup_status(cache.status) == "DONE"


def _cache_is_started(cache: ClickUpTaskCache) -> bool:
    if _cache_is_done(cache):
        return True
    if (cache.percent_complete or 0) > 0:
        return True
    return classify_clickup_status(cache.status) == "IN PROGRESS"


def _is_item_done(
    row: Milestone,
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
) -> bool:
    mc = milestone_cache or {}
    cache = _cache_for_milestone(row, tasks_by_id, mc)
    if cache and _cache_is_done(cache):
        return True
    return _row_clickup_pct(row, tasks_by_id, mc) >= 100.0


def _is_item_started(
    row: Milestone,
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
) -> bool:
    mc = milestone_cache or {}
    if _is_item_done(row, tasks_by_id, mc):
        return True
    cache = _cache_for_milestone(row, tasks_by_id, mc)
    if not cache:
        return False
    return _cache_is_started(cache)


def _normalize_phase_list_name(name: str) -> str:
    n = (name or "").strip().lower()
    for suffix in (" (iterative)", " - iterative"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def _list_name_from_clickup_cache(cache: ClickUpTaskCache) -> str:
    raw = cache.raw_json if isinstance(cache.raw_json, dict) else {}
    lst = raw.get("list")
    if isinstance(lst, dict):
        return str(lst.get("name") or "")
    return ""


def _phase_id_for_cache(
    cache: ClickUpTaskCache,
    phases: list[Milestone],
    by_id: dict[int, Milestone],
    caches_by_tid: dict[str, ClickUpTaskCache] | None = None,
) -> int | None:
    by_tid = caches_by_tid or {}

    if cache.parent_task_id:
        parent = by_tid.get(cache.parent_task_id)
        if parent:
            inherited = _phase_id_for_cache(parent, phases, by_id, by_tid)
            if inherited is not None:
                return inherited

    if cache.milestone_id and cache.milestone_id in by_id:
        cur: Milestone | None = by_id[cache.milestone_id]
        while cur:
            if cur.item_type == TimelineItemType.phase:
                return cur.id
            cur = by_id.get(cur.parent_id) if cur.parent_id else None

    lid = cache.clickup_list_id or ""
    list_matches = [p for p in phases if lid and (p.clickup_list_id or "") == lid]
    if len(list_matches) == 1:
        return list_matches[0].id

    list_label = _normalize_phase_list_name(_list_name_from_clickup_cache(cache))
    if list_label:
        for p in phases:
            if _normalize_phase_list_name(p.name) == list_label:
                return p.id
        for p in phases:
            pn = _normalize_phase_list_name(p.name)
            if pn and (list_label.startswith(pn) or pn.startswith(list_label)):
                return p.id

    return None


def _clickup_roots_for_phase(
    phase: Milestone,
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
    linked_clickup_ids: set[str],
) -> list[ClickUpTaskCache]:
    phases = [m for m in milestones if m.item_type == TimelineItemType.phase]
    by_id = {m.id: m for m in milestones}
    by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    all_ids = set(by_tid.keys())
    roots: list[ClickUpTaskCache] = []
    for c in caches:
        if c.clickup_task_id in linked_clickup_ids:
            continue
        pid = c.parent_task_id
        if pid and pid in all_ids:
            continue
        if _phase_id_for_cache(c, phases, by_id, by_tid) != phase.id:
            continue
        roots.append(c)
    return sorted(roots, key=lambda x: (x.name or "", x.id))


def _clickup_subcaches(
    parent_id: str, caches: list[ClickUpTaskCache]
) -> list[ClickUpTaskCache]:
    return sorted(
        [c for c in caches if c.parent_task_id == parent_id],
        key=lambda x: (x.name or "", x.id),
    )


def _clickup_branch_pct(cache: ClickUpTaskCache, caches: list[ClickUpTaskCache]) -> float:
    subs = _clickup_subcaches(cache.clickup_task_id, caches)
    pct = task_cache_progress_pct(cache, subtasks=subs if subs else None)
    return float(pct or 0.0)


def phase_progress_contributors(
    phase: Milestone,
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
) -> list[tuple[float, float]]:
    """Weighted progress inputs for a phase (PDC required items + unlinked ClickUp roots)."""
    mc = milestone_cache or {}
    linked = {m.clickup_task_id for m in milestones if m.clickup_task_id}
    counted_cu: set[str] = set()
    out: list[tuple[float, float]] = []

    for item in required_items_for_phase(phase, milestones):
        w = item.weight_pct if item.weight_pct > 0 else 1.0
        pct = row_clickup_progress_pct(item, milestones, tasks_by_id, mc, caches=caches)
        out.append((w, pct))
        cache = _cache_for_milestone(item, tasks_by_id, mc)
        if cache:
            counted_cu.add(cache.clickup_task_id)

    for root in _clickup_roots_for_phase(phase, milestones, caches, linked):
        if root.clickup_task_id in counted_cu:
            continue
        out.append((1.0, _clickup_branch_pct(root, caches)))

    return out


def required_items_for_phase(phase: Milestone, milestones: list[Milestone]) -> list[Milestone]:
    """Required work items under a phase (subtasks replace parent task when present)."""
    out: list[Milestone] = []
    for child in milestones:
        if child.parent_id != phase.id:
            continue
        if child.item_type == TimelineItemType.milestone:
            out.append(child)
        elif child.item_type == TimelineItemType.task:
            subtasks = [
                s
                for s in milestones
                if s.parent_id == child.id and s.item_type == TimelineItemType.subtask
            ]
            if subtasks:
                out.extend(subtasks)
            else:
                out.append(child)
    return out


def phase_workflow_status(
    phase: Milestone,
    milestones: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
    *,
    caches: list[ClickUpTaskCache] | None = None,
) -> str:
    mc = milestone_cache or {}
    cache_list = caches or []
    linked = {m.clickup_task_id for m in milestones if m.clickup_task_id}
    done_flags: list[bool] = []
    started_flags: list[bool] = []

    required = required_items_for_phase(phase, milestones)
    for r in required:
        done_flags.append(_is_item_done(r, tasks_by_id, mc))
        started_flags.append(_is_item_started(r, tasks_by_id, mc))

    for root in _clickup_roots_for_phase(phase, milestones, cache_list, linked):
        branch = _clickup_branch_pct(root, cache_list)
        done_flags.append(branch >= 100.0)
        started_flags.append(branch > 0 or _cache_is_started(root))

    if not done_flags:
        return "NOT STARTED"
    if all(done_flags):
        return "COMPLETED"
    if any(started_flags):
        return "IN PROGRESS"
    return "NOT STARTED"


def _task_branch_clickup_pct(
    task: Milestone,
    milestones: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
    *,
    caches: list[ClickUpTaskCache] | None = None,
) -> float:
    subtasks = [
        s
        for s in milestones
        if s.parent_id == task.id and s.item_type == TimelineItemType.subtask
    ]
    if subtasks:
        sw = sum(s.weight_pct for s in subtasks) or 0.0
        if sw <= 0:
            return _row_clickup_pct(task, tasks_by_id, milestone_cache)
        return sum(
            _row_clickup_pct(s, tasks_by_id, milestone_cache) * (s.weight_pct / sw)
            for s in subtasks
        )
    mc = milestone_cache or {}
    cache = _cache_for_milestone(task, tasks_by_id, mc)
    if cache and caches:
        cu_subs = _clickup_subcaches(cache.clickup_task_id, caches)
        if cu_subs:
            return _clickup_branch_pct(cache, caches)
    return _row_clickup_pct(task, tasks_by_id, milestone_cache)


def _phase_clickup_pct(
    phase: Milestone,
    milestones: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
    *,
    caches: list[ClickUpTaskCache] | None = None,
) -> float:
    contributors = phase_progress_contributors(
        phase, milestones, caches or [], tasks_by_id, milestone_cache
    )
    if contributors:
        total_w = sum(w for w, _ in contributors)
        if total_w <= 0:
            return round(sum(p for _, p in contributors) / len(contributors), 2)
        return round(sum(w * p for w, p in contributors) / total_w, 2)
    return _row_clickup_pct(phase, tasks_by_id, milestone_cache)


def row_clickup_progress_pct(
    row: Milestone,
    milestones: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
    *,
    caches: list[ClickUpTaskCache] | None = None,
) -> float:
    if row.item_type == TimelineItemType.phase:
        return _phase_clickup_pct(
            row, milestones, tasks_by_id, milestone_cache, caches=caches
        )
    if row.item_type == TimelineItemType.task:
        return round(
            _task_branch_clickup_pct(
                row, milestones, tasks_by_id, milestone_cache, caches=caches
            ),
            2,
        )
    return round(_row_clickup_pct(row, tasks_by_id, milestone_cache), 2)


def timeline_duration_days(
    m: Milestone,
    display_start: date | None,
    display_end: date | None,
    db: Session | None = None,
) -> int | None:
    from app.services.business_calendar import count_business_days_inclusive

    if m.duration_days is not None and m.duration_days > 0:
        return int(m.duration_days)
    if m.item_type == TimelineItemType.milestone:
        return 0
    if display_start and display_end:
        return count_business_days_inclusive(display_start, display_end, db)
    if m.start_date and m.target_date:
        return count_business_days_inclusive(m.start_date, m.target_date, db)
    return None


def _clickup_dates_from_cache(cache: ClickUpTaskCache | None) -> tuple[date | None, date | None]:
    if not cache:
        return None, None
    end = cache.due_date
    start: date | None = None
    raw = cache.raw_json if isinstance(cache.raw_json, dict) else {}
    from app.services.clickup import _parse_date

    start = _parse_date(raw.get("start_date"))
    if end is None:
        end = _parse_date(raw.get("due_date"))
    return start, end


def enrich_milestone_clickup_fields(
    m: Milestone,
    milestones: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache] | None = None,
    *,
    caches: list[ClickUpTaskCache] | None = None,
    db: Session | None = None,
) -> dict:
    mc = milestone_cache or {}
    cache_list = caches or []
    cache = _cache_for_milestone(m, tasks_by_id, mc)
    cu_start, cu_end = _clickup_dates_from_cache(cache)
    display_start = m.start_date
    display_end = m.target_date
    linked = cache is not None
    if m.item_type == TimelineItemType.phase:
        progress = row_clickup_progress_pct(
            m, milestones, tasks_by_id, mc, caches=cache_list
        )
        display_status = phase_workflow_status(
            m, milestones, tasks_by_id, mc, caches=cache_list
        )
        status_raw = None
    elif linked:
        progress = row_clickup_progress_pct(m, milestones, tasks_by_id, mc, caches=cache_list)
        status_raw = cache.status if cache else None
        display_status = classify_clickup_status(status_raw)
    else:
        progress = None
        display_status = None
        status_raw = None
    dur = timeline_duration_days(m, display_start, display_end, db=db)
    effective_task_id = m.clickup_task_id or (cache.clickup_task_id if cache else None)
    return {
        "clickup_task_id": effective_task_id,
        "clickup_name": cache.name if cache else None,
        "clickup_status": display_status,
        "clickup_status_raw": status_raw,
        "clickup_url": cache.url if cache else None,
        "clickup_due_date": cu_end.isoformat() if cu_end else None,
        "clickup_progress_pct": progress,
        "display_start": display_start.isoformat() if display_start else None,
        "display_end": display_end.isoformat() if display_end else None,
        "display_duration_days": dur,
    }


def clickup_actual_progress_pct(db: Session, project: Project, as_of: date) -> float | None:
    if not project.clickup_enabled:
        return None
    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
    )
    phases = [
        m
        for m in milestones
        if m.parent_id is None and m.item_type == TimelineItemType.phase
    ]
    if not phases:
        phases = [
            m
            for m in milestones
            if m.parent_id is None and m.item_type == TimelineItemType.milestone
        ]
    linked = [p for p in phases if p.clickup_list_id]
    if not linked:
        linked = [m for m in milestones if m.clickup_task_id and m.weight_pct > 0]
    if linked:
        caches = list(
            db.scalars(
                select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project.id)
            ).all()
        )
        tasks, milestone_cache = build_clickup_lookups(milestones, caches)
        total_w = sum(p.weight_pct for p in linked) or 0.0
        if total_w <= 0:
            return None
        done = 0.0
        for p in linked:
            pct = _phase_clickup_pct(
                p, milestones, tasks, milestone_cache, caches=caches
            )
            done += p.weight_pct * (pct / 100.0)
        return round(done / total_w * 100, 2)

    tasks = db.scalars(
        select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project.id)
    ).all()
    if not tasks:
        return None
    total_est = sum(t.time_estimate_ms or 0 for t in tasks)
    if total_est > 0:
        done = sum(
            (t.time_estimate_ms or 0)
            for t in tasks
            if t.is_closed or (t.percent_complete or 0) >= 100
        )
        return round(done / total_est * 100, 2)
    closed = sum(1 for t in tasks if t.is_closed)
    return round(closed / len(tasks) * 100, 2)


def milestone_actual_progress_pct(db: Session, project_id: int, as_of: date) -> float:
    milestones = db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    from app.services.schedule import _progress_weight_rows

    prog = _progress_weight_rows(list(milestones))
    total = sum(m.weight_pct for m in prog) or 0.0
    if total <= 0:
        return 0.0
    done = sum(
        m.weight_pct
        for m in prog
        if m.status == MilestoneStatus.done and m.actual_date and m.actual_date <= as_of
    )
    return round(done / total * 100, 2)


def resolve_actual_progress(db: Session, project: Project, as_of: date) -> float:
    cu = clickup_actual_progress_pct(db, project, as_of)
    if cu is not None:
        return cu
    return milestone_actual_progress_pct(db, project.id, as_of)
