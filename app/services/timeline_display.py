"""Timeline view: PDC milestones + extra ClickUp tasks per list/phase."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Milestone, Project, TimelineItemType
from app.services.clickup_milestone_dates import timeline_baseline_locked
from app.services.business_calendar import count_business_days_inclusive
from app.services.clickup import _cache_start_date, _parent_date_span
from app.services.progress import (
    _clickup_roots_for_phase,
    build_clickup_lookups,
    classify_clickup_status,
    phase_workflow_status,
    row_clickup_progress_pct,
    task_cache_progress_pct,
    timeline_phases,
)


def _cache_date_span(cache: ClickUpTaskCache) -> tuple[date | None, date | None]:
    start = _cache_start_date(cache)
    end = cache.due_date
    return start, end


def _span_from_bounds(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    if start and end:
        return start, end
    if start:
        return start, start
    if end:
        return end, end
    return None, None


def resolve_clickup_baseline_dates(
    cache: ClickUpTaskCache,
    *,
    phase: Milestone,
    parent_clickup_task_id: str | None,
    caches_by_tid: dict[str, ClickUpTaskCache],
    ms_by_id: dict[int, Milestone],
    ms_by_clickup: dict[str, Milestone],
    pdc_parent: Milestone | None = None,
    project: Project | None = None,
) -> tuple[date | None, date | None]:
    """Subtask → parent PDC/task span only (never subtask's own ClickUp dates)."""
    is_subtask = bool(parent_clickup_task_id or cache.parent_task_id or pdc_parent)
    if is_subtask:
        if pdc_parent and (pdc_parent.start_date or pdc_parent.target_date):
            span = _span_from_bounds(pdc_parent.start_date, pdc_parent.target_date)
            if span[0] or span[1]:
                return span
        parent_tid = parent_clickup_task_id or cache.parent_task_id
        if parent_tid:
            parent = caches_by_tid.get(parent_tid)
            if parent:
                ps, pe, _ = _parent_date_span(
                    parent,
                    ms_by_id=ms_by_id,
                    ms_by_clickup=ms_by_clickup,
                    protect_pdc_baseline=timeline_baseline_locked(project),
                )
                span = _span_from_bounds(ps, pe)
                if span[0] or span[1]:
                    return span
        inherited = _span_from_bounds(phase.start_date, phase.target_date)
        if inherited[0] or inherited[1]:
            return inherited
        return None, None
    linked = ms_by_clickup.get(cache.clickup_task_id or "")
    if linked:
        if linked.start_date or linked.target_date:
            return _span_from_bounds(linked.start_date, linked.target_date)
        inherited = _span_from_bounds(phase.start_date, phase.target_date)
        if inherited[0] or inherited[1]:
            return inherited
        if timeline_baseline_locked(project):
            return None, None
    own = _span_from_bounds(*_cache_date_span(cache))
    if own[0] or own[1]:
        return own
    inherited = _span_from_bounds(phase.start_date, phase.target_date)
    if inherited[0] or inherited[1]:
        return inherited
    return own


def business_duration_days(
    start: date | None,
    end: date | None,
    db: Session | None,
    *,
    stored_duration: int | None = None,
) -> int | None:
    if stored_duration is not None and stored_duration > 0:
        return int(stored_duration)
    if start and end:
        return count_business_days_inclusive(start, end, db)
    return None


def _phase_descendants(phase_id: int, milestones: list[Milestone]) -> list[Milestone]:
    by_parent: dict[int | None, list[Milestone]] = {}
    for m in milestones:
        by_parent.setdefault(m.parent_id, []).append(m)
    out: list[Milestone] = []

    def walk(pid: int) -> None:
        for ch in sorted(by_parent.get(pid, []), key=lambda x: (x.sort_order, x.id)):
            out.append(ch)
            walk(ch.id)

    walk(phase_id)
    return out


def _clickup_children(
    parent_id: str, caches: list[ClickUpTaskCache], linked_clickup_ids: set[str]
) -> list[ClickUpTaskCache]:
    return sorted(
        [
            c
            for c in caches
            if c.parent_task_id == parent_id and c.clickup_task_id not in linked_clickup_ids
        ],
        key=lambda x: (x.name or "", x.id),
    )


def _extra_row_dict(
    cache: ClickUpTaskCache,
    *,
    phase: Milestone,
    phase_status: str,
    ms_list: list[Milestone],
    tasks_by_id: dict[str, ClickUpTaskCache],
    milestone_cache: dict[int, ClickUpTaskCache],
    depth: int,
    parent_clickup_task_id: str | None,
    sort_order: int,
    parent_id: int | None,
    cu_subtasks: list[ClickUpTaskCache] | None = None,
    caches_by_tid: dict[str, ClickUpTaskCache] | None = None,
    ms_by_id: dict[int, Milestone] | None = None,
    ms_by_clickup: dict[str, Milestone] | None = None,
    db: Session | None = None,
    project: Project | None = None,
) -> dict:
    pct = task_cache_progress_pct(cache, subtasks=cu_subtasks if cu_subtasks else None)
    by_tid = caches_by_tid or {}
    pdc_parent = None
    if parent_clickup_task_id and ms_by_clickup:
        pdc_parent = ms_by_clickup.get(parent_clickup_task_id)
    t_start, t_end = resolve_clickup_baseline_dates(
        cache,
        phase=phase,
        parent_clickup_task_id=parent_clickup_task_id,
        caches_by_tid=by_tid,
        ms_by_id=ms_by_id or {},
        ms_by_clickup=ms_by_clickup or {},
        pdc_parent=pdc_parent,
        project=project,
    )
    dur = business_duration_days(t_start, t_end, db)
    inherited_dates = bool(parent_clickup_task_id and t_start and t_end)
    return {
        "id": -cache.id,
        "name": cache.name,
        "module": None,
        "start_date": t_start.isoformat() if t_start else None,
        "target_date": t_end.isoformat() if t_end else None,
        "weight_pct": 0.0,
        "status": "open",
        "actual_date": None,
        "is_payment_milestone": False,
        "duration_days": dur,
        "item_type": "subtask" if parent_clickup_task_id else "task",
        "parent_id": parent_id,
        "sort_order": sort_order,
        "clickup_task_id": cache.clickup_task_id,
        "clickup_name": cache.name,
        "clickup_status": classify_clickup_status(cache.status),
        "clickup_status_raw": cache.status,
        "clickup_url": cache.url,
        "clickup_due_date": cache.due_date.isoformat() if cache.due_date else None,
        "clickup_progress_pct": pct,
        "display_start": t_start.isoformat() if t_start else None,
        "display_end": t_end.isoformat() if t_end else None,
        "display_duration_days": dur,
        "timeline_dates_inherited": inherited_dates,
        "clickup_only": True,
        "phase_id": phase.id,
        "phase_name": phase.name,
        "phase_status": phase_status,
        "parent_clickup_task_id": parent_clickup_task_id,
        "depth": depth,
        "expandable": False,
    }


def merge_timeline_with_clickup(
    pdc_rows: list[dict],
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
    db: Session | None = None,
    project: Project | None = None,
) -> list[dict]:
    """Ordered flat rows: phase block = PDC rows then ClickUp-only tasks for that list."""
    tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)
    by_id = {m.id: m for m in milestones}
    linked_cu = {m.clickup_task_id for m in milestones if m.clickup_task_id}
    caches_by_tid = {c.clickup_task_id: c for c in caches if c.clickup_task_id}
    ms_by_id = {m.id: m for m in milestones}
    ms_by_clickup = {m.clickup_task_id: m for m in milestones if m.clickup_task_id}
    pdc_ids = {r["id"] for r in pdc_rows if isinstance(r.get("id"), int) and r["id"] > 0}

    phases = timeline_phases(milestones)
    row_by_id = {r["id"]: r for r in pdc_rows}

    def pdc_row(mid: int) -> dict | None:
        return row_by_id.get(mid)

    display: list[dict] = []
    seq = 0

    def append_row(row: dict) -> None:
        nonlocal seq
        seq += 1
        row = {**row, "timeline_seq": seq}
        display.append(row)

    def append_clickup_tree(
        cache: ClickUpTaskCache,
        *,
        phase: Milestone,
        phase_status: str,
        depth: int,
        parent_clickup: str | None,
        parent_pdc_id: int | None,
        base_sort: int,
    ) -> None:
        subs = _clickup_children(cache.clickup_task_id, caches, linked_cu)
        row = _extra_row_dict(
            cache,
            phase=phase,
            phase_status=phase_status,
            ms_list=milestones,
            tasks_by_id=tasks_by_id,
            milestone_cache=milestone_cache,
            depth=depth,
            parent_clickup_task_id=parent_clickup,
            sort_order=base_sort,
            parent_id=parent_pdc_id,
            cu_subtasks=subs if subs else None,
            caches_by_tid=caches_by_tid,
            ms_by_id=ms_by_id,
            ms_by_clickup=ms_by_clickup,
            db=db,
            project=project,
        )
        row["expandable"] = len(subs) > 0
        append_row(row)
        for i, sub in enumerate(subs):
            append_clickup_tree(
                sub,
                phase=phase,
                phase_status=phase_status,
                depth=depth + 1,
                parent_clickup=cache.clickup_task_id,
                parent_pdc_id=parent_pdc_id,
                base_sort=base_sort + i + 1,
            )

    for phase in phases:
        pr = pdc_row(phase.id)
        phase_status = phase_workflow_status(
            phase, milestones, tasks_by_id, milestone_cache, caches=caches
        )
        phase_pct = row_clickup_progress_pct(
            phase, milestones, tasks_by_id, milestone_cache, caches=caches
        )
        if pr:
            append_row(
                {
                    **pr,
                    "phase_id": phase.id,
                    "depth": 0,
                    "expandable": True,
                    "clickup_progress_pct": phase_pct,
                    "clickup_status": phase_status,
                }
            )

        descendants = _phase_descendants(phase.id, milestones)

        for m in descendants:
            mr = pdc_row(m.id)
            if not mr:
                continue
            depth = 1
            parent_m = by_id.get(m.parent_id) if m.parent_id else None
            if parent_m and parent_m.item_type == TimelineItemType.task:
                depth = 2
            elif m.parent_id == phase.id:
                depth = 1
            row_pct = row_clickup_progress_pct(
                m, milestones, tasks_by_id, milestone_cache, caches=caches
            )
            patched = {
                **mr,
                "phase_id": phase.id,
                "depth": depth,
                "expandable": False,
                "clickup_progress_pct": row_pct,
            }
            p_cu = milestone_cache.get(m.id)
            parent_m = by_id.get(m.parent_id) if m.parent_id else None
            is_clickup_subtask = bool(
                m.item_type == TimelineItemType.subtask
                or (p_cu and p_cu.parent_task_id)
            )
            needs_dates = p_cu and (
                is_clickup_subtask
                or (not patched.get("start_date") and not patched.get("target_date"))
            )
            if needs_dates:
                parent_tid = p_cu.parent_task_id if is_clickup_subtask else None
                if not parent_tid and parent_m and parent_m.clickup_task_id:
                    parent_tid = parent_m.clickup_task_id
                ts, te = resolve_clickup_baseline_dates(
                    p_cu,
                    phase=phase,
                    parent_clickup_task_id=parent_tid,
                    caches_by_tid=caches_by_tid,
                    ms_by_id=ms_by_id,
                    ms_by_clickup=ms_by_clickup,
                    pdc_parent=parent_m if is_clickup_subtask else None,
                    project=project,
                )
                if not ts and not te and m.item_type == TimelineItemType.task:
                    ts, te = _span_from_bounds(phase.start_date, phase.target_date)
                elif not ts and not te and m.parent_id:
                    parent_m = by_id.get(m.parent_id)
                    if parent_m:
                        ts, te = _span_from_bounds(
                            parent_m.start_date, parent_m.target_date
                        )
                if ts or te:
                    ts, te = _span_from_bounds(ts, te)
                    patched["start_date"] = ts.isoformat() if ts else None
                    patched["target_date"] = te.isoformat() if te else None
                    patched["display_start"] = patched["start_date"]
                    patched["display_end"] = patched["target_date"]
                    dur = business_duration_days(
                        ts,
                        te,
                        db,
                        stored_duration=m.duration_days,
                    )
                    patched["duration_days"] = dur
                    patched["display_duration_days"] = dur
                    if is_clickup_subtask and parent_tid:
                        patched["timeline_dates_inherited"] = True
            elif patched.get("start_date") and patched.get("target_date"):
                ts = date.fromisoformat(str(patched["start_date"])[:10])
                te = date.fromisoformat(str(patched["target_date"])[:10])
                dur = business_duration_days(
                    ts, te, db, stored_duration=m.duration_days
                )
                patched["duration_days"] = dur
                patched["display_duration_days"] = dur
            append_row(patched)
            # ClickUp subtasks under linked PDC task (not in WBS)
            pdc_cache = milestone_cache.get(m.id)
            if pdc_cache:
                extras = _clickup_children(pdc_cache.clickup_task_id, caches, linked_cu)
                for i, ex in enumerate(extras):
                    append_clickup_tree(
                        ex,
                        phase=phase,
                        phase_status=phase_status,
                        depth=depth + 1,
                        parent_clickup=pdc_cache.clickup_task_id,
                        parent_pdc_id=m.id,
                        base_sort=m.sort_order + 500 + i,
                    )

        cu_roots = _clickup_roots_for_phase(phase, milestones, caches, linked_cu)
        for i, root in enumerate(cu_roots):
            append_clickup_tree(
                root,
                phase=phase,
                phase_status=phase_status,
                depth=1,
                parent_clickup=None,
                parent_pdc_id=phase.id,
                base_sort=phase.sort_order + 900 + i,
            )

    # Phases / rows not walked (safety)
    for r in pdc_rows:
        if r["id"] not in {x["id"] for x in display}:
            append_row({**r, "depth": 0, "expandable": False})

    return display


def _milestone_pdc_row_dict(m: Milestone) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "module": m.module,
        "start_date": m.start_date.isoformat() if m.start_date else None,
        "target_date": m.target_date.isoformat() if m.target_date else None,
        "weight_pct": float(m.weight_pct or 0),
        "status": m.status.value,
        "actual_date": m.actual_date.isoformat() if m.actual_date else None,
        "is_payment_milestone": bool(m.is_payment_milestone),
        "duration_days": m.duration_days,
        "item_type": m.item_type.value,
        "parent_id": m.parent_id,
        "sort_order": int(m.sort_order or 0),
        "clickup_task_id": m.clickup_task_id,
    }


def filter_timeline_display_rows(
    rows: list[dict],
    *,
    include_subtasks: bool = False,
) -> list[dict]:
    """Optional row-level filter for report/API consumers."""
    if include_subtasks:
        return rows
    return [
        r
        for r in rows
        if (r.get("item_type") or "") != TimelineItemType.subtask.value
    ]


def timeline_display_rows_for_project(
    db: Session,
    project_id: int,
    *,
    include_subtasks: bool = False,
) -> list[dict]:
    """Timeline + ClickUp merge (same order as GET /milestones when unfiltered).

    Default ``include_subtasks=False`` drops subtask rows for report slides/sheets.
    Pass ``include_subtasks=True`` for full WBS (e.g. Excel weight rows, UI parity).
    """
    from sqlalchemy import select

    from app.models import ClickUpTaskCache, Project
    from app.services.progress import (
        build_clickup_lookups,
        clickup_status_mapping_context,
        enrich_milestone_clickup_fields,
    )

    project = db.get(Project, project_id)
    ms_list = list(
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
    with clickup_status_mapping_context(db):
        tasks_by_id, milestone_cache = build_clickup_lookups(ms_list, caches)
        pdc_rows: list[dict] = []
        for m in ms_list:
            base = _milestone_pdc_row_dict(m)
            base.update(
                enrich_milestone_clickup_fields(
                    m,
                    ms_list,
                    tasks_by_id,
                    milestone_cache,
                    caches=caches,
                    db=db,
                )
            )
            pdc_rows.append(base)
        merged = merge_timeline_with_clickup(
            pdc_rows, ms_list, caches, db=db, project=project
        )
        return filter_timeline_display_rows(merged, include_subtasks=include_subtasks)
