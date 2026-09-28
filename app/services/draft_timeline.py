from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Project, ScheduleBaselineMilestone, TimelineItemType
from app.services.business_calendar import add_business_days, business_day_after
from app.services.project_lifecycle import draft_timeline_editable
from app.services.schedule import get_draft_baseline, get_or_revive_sph_draft_baseline
from app.services.timeline_item_type import parse_timeline_item_type
from app.services.timeline_validation import validate_timeline_items


def _parse_optional_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _row_out(
    r: ScheduleBaselineMilestone,
    id_to_row_key: dict[int, str] | None = None,
) -> dict:
    parent_ref = None
    if r.parent_id and id_to_row_key:
        parent_ref = id_to_row_key.get(r.parent_id)
    return {
        "id": r.id,
        "row_key": r.row_key,
        "name": r.name,
        "start_date": r.start_date.isoformat() if r.start_date else None,
        "target_date": r.target_date.isoformat() if r.target_date else None,
        "duration_days": r.duration_days,
        "weight_pct": r.weight_pct,
        "is_payment_milestone": r.is_payment_milestone,
        "item_type": r.item_type.value if r.item_type else "phase",
        "parent_id": r.parent_id,
        "parent_ref": parent_ref,
        "sort_order": r.sort_order,
    }


def list_draft_rows(db: Session, project_id: int) -> list[dict]:
    draft = get_draft_baseline(db, project_id)
    if not draft:
        draft = get_or_revive_sph_draft_baseline(db, project_id)
    if not draft:
        return []
    rows = list(
        db.scalars(
            select(ScheduleBaselineMilestone)
            .where(ScheduleBaselineMilestone.baseline_id == draft.id)
            .order_by(ScheduleBaselineMilestone.sort_order, ScheduleBaselineMilestone.id)
        ).all()
    )
    id_to_row_key: dict[int, str] = {}
    for r in rows:
        if r.id is not None and r.row_key:
            id_to_row_key[r.id] = r.row_key
    return [_row_out(r, id_to_row_key) for r in rows]


def _resolve_draft_row_parents(rows: list[dict]) -> list[dict]:
    """Fill parent_ref / parent_key from parent_id and row_key map before validate/save."""
    id_to_key: dict[int, str] = {}
    key_set: set[str] = set()
    for raw in rows:
        rk = str(raw.get("row_key") or raw.get("id") or "").strip()
        if raw.get("id") is not None:
            try:
                id_to_key[int(raw["id"])] = rk or id_to_key.get(int(raw["id"]), "")
            except (TypeError, ValueError):
                pass
        if rk:
            key_set.add(rk)
            id_to_key[rk] = rk

    out: list[dict] = []
    for raw in rows:
        r = dict(raw)
        pref = r.get("parent_ref") or r.get("parent_row_key")
        if not pref and r.get("parent_id") is not None:
            try:
                pid = int(r["parent_id"])
                pref = id_to_key.get(pid)
            except (TypeError, ValueError):
                pref = None
        r["parent_ref"] = pref
        out.append(r)
    return out


def _children(
    by_id: dict[int, ScheduleBaselineMilestone], parent_id: int
) -> list[ScheduleBaselineMilestone]:
    return sorted(
        [r for r in by_id.values() if r.parent_id == parent_id],
        key=lambda x: (x.sort_order, x.id),
    )


def _apply_subtask_dates(
    row: ScheduleBaselineMilestone, start: date, db: Session
) -> None:
    dur = max(row.duration_days or 1, 1)
    row.start_date = add_business_days(start, 1, db)
    row.target_date = add_business_days(row.start_date, dur, db)


def _apply_task_dates(
    row: ScheduleBaselineMilestone,
    start: date,
    db: Session,
    by_id: dict[int, ScheduleBaselineMilestone],
) -> None:
    subtasks = [
        c
        for c in _children(by_id, row.id)
        if c.item_type == TimelineItemType.subtask
    ]
    if subtasks:
        cursor = start
        for st in subtasks:
            _apply_subtask_dates(st, cursor, db)
            if st.target_date:
                cursor = business_day_after(st.target_date, db)
        starts = [s.start_date for s in subtasks if s.start_date]
        ends = [s.target_date for s in subtasks if s.target_date]
        row.start_date = min(starts) if starts else add_business_days(start, 1, db)
        row.target_date = max(ends) if ends else row.start_date
    else:
        dur = max(row.duration_days or 1, 1)
        row.start_date = add_business_days(start, 1, db)
        row.target_date = add_business_days(row.start_date, dur, db)


def _apply_milestone_gate(
    row: ScheduleBaselineMilestone,
    default_day: date,
    manual: date | None,
) -> None:
    d = manual or default_day
    row.start_date = d
    row.target_date = d
    row.duration_days = 0


def _apply_phase_dates(
    row: ScheduleBaselineMilestone,
    phase_start: date,
    db: Session,
    by_id: dict[int, ScheduleBaselineMilestone],
    milestone_manual: dict[int, date | None],
) -> None:
    kids = _children(by_id, row.id)
    tasks = [c for c in kids if c.item_type == TimelineItemType.task]
    gates = [c for c in kids if c.item_type == TimelineItemType.milestone]

    phase_work_start = add_business_days(phase_start, 1, db)
    dur = max(row.duration_days or 1, 1)
    duration_end = add_business_days(phase_work_start, dur, db)

    cursor = phase_start
    for task in tasks:
        _apply_task_dates(task, cursor, db, by_id)
        if task.target_date:
            cursor = business_day_after(task.target_date, db)

    if tasks:
        last_day = max(
            (t.target_date for t in tasks if t.target_date),
            default=phase_work_start,
        )
    else:
        # Phase tanpa task (hanya milestone gate): rentang dari durasi + kalender kerja
        last_day = duration_end

    for gate in gates:
        manual = milestone_manual.get(gate.id)
        _apply_milestone_gate(gate, last_day, manual)

    span_starts: list[date] = []
    span_ends: list[date] = []
    for c in tasks + gates:
        if c.start_date:
            span_starts.append(c.start_date)
        if c.target_date:
            span_ends.append(c.target_date)

    if tasks:
        if span_starts and span_ends:
            row.start_date = min(span_starts)
            row.target_date = max(span_ends)
        else:
            row.start_date = phase_work_start
            row.target_date = duration_end
    else:
        row.start_date = phase_work_start
        row.target_date = duration_end
        if span_starts:
            row.start_date = min(row.start_date, min(span_starts))
        if span_ends:
            row.target_date = max(row.target_date, max(span_ends))


def recalc_draft_dates(
    db: Session,
    project_id: int,
    start: date,
    milestone_manual: dict[int, date | None] | None = None,
) -> None:
    draft = get_draft_baseline(db, project_id)
    if not draft:
        return
    rows = list(
        db.scalars(
            select(ScheduleBaselineMilestone)
            .where(ScheduleBaselineMilestone.baseline_id == draft.id)
            .order_by(ScheduleBaselineMilestone.sort_order, ScheduleBaselineMilestone.id)
        ).all()
    )
    by_id = {r.id: r for r in rows}
    manual = milestone_manual or {}
    top = [
        r
        for r in rows
        if not r.parent_id
        and r.item_type in (TimelineItemType.phase, TimelineItemType.milestone)
    ]
    cursor = start
    for row in sorted(top, key=lambda x: (x.sort_order, x.id)):
        if row.item_type == TimelineItemType.milestone:
            row.item_type = TimelineItemType.phase
        _apply_phase_dates(row, cursor, db, by_id, manual)
        if row.target_date:
            cursor = business_day_after(row.target_date, db)


def save_draft_rows(
    db: Session,
    project: Project,
    rows: list[dict],
    start: date | None,
) -> list[dict]:
    from app.models import ProjectSph

    sph = db.get(ProjectSph, project.id)
    if not draft_timeline_editable(project, sph=sph):
        raise ValueError(
            "Timeline draft read-only — selesaikan di SPH, atau sudah dikonfirmasi di Kick Off"
        )
    draft = get_draft_baseline(db, project.id)
    if not draft:
        raise ValueError("Draft timeline belum ada — generate dari SPH")

    rows = _resolve_draft_row_parents(rows)

    validate_items = []
    for raw in rows:
        it = str(raw.get("item_type") or "phase")
        dur_raw = raw.get("duration_days")
        if it == "milestone":
            dur = 0
        elif dur_raw is not None:
            dur = int(dur_raw)
        else:
            dur = 1
        validate_items.append(
            {
                "row_key": str(raw.get("row_key") or raw.get("id") or f"row_{raw.get('sort_order')}"),
                "name": raw.get("name"),
                "item_type": it,
                "weight_pct": raw.get("weight_pct") or 0,
                "parent_key": raw.get("parent_ref") or raw.get("parent_row_key"),
                "duration_days": dur,
            }
        )
    validate_timeline_items(validate_items)

    if start:
        draft.effective_from = start
    db.execute(
        delete(ScheduleBaselineMilestone).where(
            ScheduleBaselineMilestone.baseline_id == draft.id
        )
    )
    db.flush()
    id_map: dict[str, int] = {}
    milestone_manual: dict[int, date | None] = {}
    normalized = sorted(rows, key=lambda x: int(x.get("sort_order") or 0))
    for raw in normalized:
        parent_ref = raw.get("parent_ref") or raw.get("parent_row_key")
        parent_id = id_map.get(str(parent_ref)) if parent_ref else None
        it = parse_timeline_item_type(str(raw.get("item_type") or "phase"))
        row_key = str(raw.get("row_key") or raw.get("id") or "")
        dur = int(raw["duration_days"]) if raw.get("duration_days") is not None else 1
        if it == TimelineItemType.milestone:
            dur = 0
        r = ScheduleBaselineMilestone(
            baseline_id=draft.id,
            row_key=row_key or None,
            name=str(raw.get("name") or "").strip() or "Item",
            duration_days=dur,
            weight_pct=float(raw.get("weight_pct") or 0),
            is_payment_milestone=bool(raw.get("is_payment_milestone")),
            item_type=it,
            parent_id=parent_id,
            sort_order=int(raw.get("sort_order") or 0),
        )
        db.add(r)
        db.flush()
        if it == TimelineItemType.milestone:
            milestone_manual[r.id] = _parse_optional_date(raw.get("target_date"))
        if row_key:
            id_map[row_key] = r.id
        id_map[str(r.id)] = r.id
    eff = start or draft.effective_from or date.today()
    recalc_draft_dates(db, project.id, eff, milestone_manual)
    return list_draft_rows(db, project.id)
