"""Unified draft timeline scheduling: durasi, predecessor, relasi FS/SS/FF/SF — semua tipe."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import ScheduleBaselineMilestone, TimelineItemType
from app.services.business_calendar import (
    add_business_days,
    business_day_after,
    count_business_days_inclusive,
    subtract_business_days,
)
from app.services.schedule_dependency import (
    link_type_requires_pred_end,
    link_type_requires_pred_start,
    parse_predecessor_link_type,
    resolve_successor_span,
)


def subtract_business_days_from_end(
    db: Session | None, end: date, duration_days: int
) -> date:
    return subtract_business_days(end, max(int(duration_days or 1), 1), db)


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


def compute_project_timeline_summary(
    db: Session | None,
    row_dicts: list[dict],
    project_start: date | None,
) -> dict:
    """
    Durasi aktual: tanggal mulai paling awal s/d tanggal akhir paling akhir baris timeline
    (phase/task/subtask; milestone gate diabaikan), hari kerja inclusive.
    """
    starts: list[date] = []
    ends: list[date] = []
    for raw in row_dicts:
        it = str(raw.get("item_type") or "phase").lower()
        if it == "milestone":
            continue
        sd = _parse_optional_date(raw.get("start_date"))
        if sd:
            starts.append(sd)
        td = _parse_optional_date(raw.get("target_date"))
        if td:
            ends.append(td)

    start = min(starts) if starts else project_start
    end = max(ends) if ends else None
    dur: int | None = None
    if start and end and end >= start:
        dur = count_business_days_inclusive(start, end, db)
    elif start and end:
        dur = count_business_days_inclusive(end, start, db)
    return {
        "project_start_date": start.isoformat() if start else None,
        "project_end_date": end.isoformat() if end else None,
        "project_duration_business_days": dur,
    }


def apply_schedule_driver_to_raw(db: Session | None, raw: dict) -> None:
    """
    Normalize row before insert: honor UI edit intent (duration / start / end).
    Mutates raw in place; does not set DB columns.
    """
    it = str(raw.get("item_type") or "phase").lower()
    if it == "milestone":
        return
    driver = str(raw.get("schedule_driver") or "").strip().lower()
    start_d = _parse_optional_date(raw.get("start_date"))
    end_d = _parse_optional_date(raw.get("target_date"))
    dur_raw = raw.get("duration_days")
    try:
        dur = max(int(dur_raw if dur_raw is not None else 1), 1)
    except (TypeError, ValueError):
        dur = 1

    if driver == "end" and start_d and end_d:
        raw["duration_days"] = count_business_days_inclusive(start_d, end_d, db)
    elif driver == "duration":
        raw["target_date"] = None
        raw["duration_days"] = dur
    elif driver == "start" and start_d:
        raw["target_date"] = None
        raw["duration_days"] = dur
    elif not driver and start_d and end_d and dur_raw is None:
        raw["duration_days"] = count_business_days_inclusive(start_d, end_d, db)


def _row_ref(row: ScheduleBaselineMilestone) -> str:
    return (row.row_key or str(row.id)).strip()


def _children(
    by_id: dict[int, ScheduleBaselineMilestone], parent_id: int
) -> list[ScheduleBaselineMilestone]:
    return sorted(
        [r for r in by_id.values() if r.parent_id == parent_id],
        key=lambda x: (x.sort_order, x.id),
    )


def _register_span(
    starts: dict[str, date],
    ends: dict[str, date],
    row: ScheduleBaselineMilestone,
) -> None:
    if not row.start_date or not row.target_date:
        return
    rk = _row_ref(row)
    starts[rk] = row.start_date
    ends[rk] = row.target_date
    if row.row_key:
        starts[str(row.id)] = row.start_date
        ends[str(row.id)] = row.target_date


def _default_start_from_siblings(
    db: Session,
    row: ScheduleBaselineMilestone,
    sched: list[ScheduleBaselineMilestone],
    ends: dict[str, date],
    by_id: dict[int, ScheduleBaselineMilestone],
    project_start: date,
) -> date:
    siblings = sorted(
        [s for s in sched if s.parent_id == row.parent_id],
        key=lambda x: (x.sort_order, x.id),
    )
    idx = next((i for i, s in enumerate(siblings) if s.id == row.id), 0)
    for j in range(idx - 1, -1, -1):
        prev = siblings[j]
        pe = ends.get(_row_ref(prev))
        if pe:
            return business_day_after(pe, db)
    if row.parent_id and row.parent_id in by_id:
        par = by_id[row.parent_id]
        ps = par.start_date
        if ps:
            return add_business_days(ps, 1, db)
    return add_business_days(project_start, 1, db)


def schedule_draft_milestone_rows(
    db: Session,
    rows: list[ScheduleBaselineMilestone],
    project_start: date,
    milestone_manual: dict[int, date | None] | None = None,
    row_inputs: dict[int, dict] | None = None,
) -> None:
    """Set start/target/duration for phase, task, subtask; then roll up; then gates."""
    by_id = {r.id: r for r in rows}
    manual = milestone_manual or {}
    sched = [
        r
        for r in rows
        if r.item_type
        in (TimelineItemType.phase, TimelineItemType.task, TimelineItemType.subtask)
    ]
    starts: dict[str, date] = {}
    ends: dict[str, date] = {}

    pending = sorted(sched, key=lambda x: (x.sort_order, x.id))
    guard = 0
    while pending and guard < len(pending) * 4 + 12:
        guard += 1
        row = pending[0]
        pred = (row.predecessor_ref or "").strip() or None
        link = parse_predecessor_link_type(row.predecessor_link_type)

        if row.parent_id and row.parent_id in by_id:
            par = by_id[row.parent_id]
            if par.item_type != TimelineItemType.milestone and _row_ref(par) not in starts:
                pending.append(pending.pop(0))
                continue

        if pred:
            if link_type_requires_pred_end(link) and pred not in ends:
                pending.append(pending.pop(0))
                continue
            if link_type_requires_pred_start(link) and pred not in starts:
                pending.append(pending.pop(0))
                continue

        dur = max(int(row.duration_days or 1), 1)
        start_d: date | None = None
        end_d: date | None = None

        if pred:
            span = resolve_successor_span(
                link,
                starts.get(pred),
                ends.get(pred),
                dur,
                db,
            )
            if span[0] and span[1]:
                start_d, end_d = span

        raw = (row_inputs or {}).get(row.id) or {}
        driver = str(raw.get("schedule_driver") or "").strip().lower()
        manual_start = _parse_optional_date(raw.get("start_date"))
        manual_end = _parse_optional_date(raw.get("target_date"))
        if start_d is None and not pred and manual_start and driver in (
            "start",
            "duration",
            "",
        ):
            start_d = add_business_days(manual_start, 1, db)
            end_d = add_business_days(start_d, dur, db)
        if (
            start_d
            and end_d is None
            and driver == "duration"
            and not pred
        ):
            end_d = add_business_days(start_d, dur, db)
        if (
            start_d
            and manual_end
            and driver == "end"
            and not pred
        ):
            end_d = add_business_days(manual_end, 1, db)
            start_d = subtract_business_days_from_end(db, end_d, dur)

        if start_d is None or end_d is None:
            start_d = _default_start_from_siblings(
                db, row, sched, ends, by_id, project_start
            )
            end_d = add_business_days(start_d, dur, db)

        row.start_date = start_d
        row.target_date = end_d
        row.duration_days = count_business_days_inclusive(start_d, end_d, db)
        _register_span(starts, ends, row)
        pending.pop(0)

    # Bottom-up rollup for containers that have schedulable children
    depth_cache: dict[int, int] = {}

    def depth(rid: int) -> int:
        if rid in depth_cache:
            return depth_cache[rid]
        r = by_id[rid]
        if not r.parent_id:
            depth_cache[rid] = 0
            return 0
        depth_cache[rid] = depth(r.parent_id) + 1
        return depth_cache[rid]

    for row in sorted(sched, key=lambda x: (-depth(x.id), x.sort_order, x.id)):
        kids = _children(by_id, row.id)
        weighted = [
            c
            for c in kids
            if c.item_type
            in (TimelineItemType.phase, TimelineItemType.task, TimelineItemType.subtask)
        ]
        if not weighted:
            continue
        cs = [c.start_date for c in weighted if c.start_date]
        ce = [c.target_date for c in weighted if c.target_date]
        if cs and ce:
            row.start_date = min(cs)
            row.target_date = max(ce)
            row.duration_days = count_business_days_inclusive(
                row.start_date, row.target_date, db
            )
            _register_span(starts, ends, row)

    gates = [r for r in rows if r.item_type == TimelineItemType.milestone]
    for gate in sorted(gates, key=lambda x: (x.sort_order, x.id)):
        parent = by_id.get(gate.parent_id) if gate.parent_id else None
        default_day = project_start
        if parent and parent.target_date:
            default_day = parent.target_date
        manual_day = manual.get(gate.id)
        d = manual_day or default_day
        gate.start_date = d
        gate.target_date = d
        gate.duration_days = 0
