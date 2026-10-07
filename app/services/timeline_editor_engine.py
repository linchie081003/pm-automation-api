"""Timeline Editor (beta): multi-predecessor scheduling + cascade on draft row dicts."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.business_calendar import (
    add_business_days,
    business_day_after,
    count_business_days_inclusive,
    subtract_business_days,
)
from app.services.schedule_dependency import (
    PredecessorLinkType,
    link_type_requires_pred_end,
    link_type_requires_pred_start,
    parse_predecessor_link_type,
    resolve_successor_span,
)
from app.services.timeline_schedule import (
    apply_schedule_driver_to_raw,
    compute_project_timeline_summary,
    subtract_business_days_from_end,
)


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


def _row_ref(raw: dict) -> str:
    rk = str(raw.get("row_key") or "").strip()
    if rk:
        return rk
    if raw.get("id") is not None:
        return str(raw["id"])
    return str(raw.get("sort_order") or "")


def normalize_predecessors(raw: dict) -> list[dict]:
    """Merge legacy single predecessor_ref with explicit predecessors list."""
    out: list[dict] = []
    for p in raw.get("predecessors") or []:
        if not isinstance(p, dict):
            continue
        ref = str(p.get("predecessor_ref") or "").strip()
        if not ref:
            continue
        try:
            lag = int(p.get("lag_days") or 0)
        except (TypeError, ValueError):
            lag = 0
        out.append(
            {
                "predecessor_ref": ref,
                "link_type": str(p.get("link_type") or "FS").strip().upper() or "FS",
                "lag_days": max(lag, 0),
            }
        )
    if out:
        return out
    legacy = str(raw.get("predecessor_ref") or "").strip()
    if legacy:
        out.append(
            {
                "predecessor_ref": legacy,
                "link_type": str(raw.get("predecessor_link_type") or "FS").strip().upper()
                or "FS",
                "lag_days": 0,
            }
        )
    return out


def _merge_multi_predecessor_span(
    db: Session | None,
    preds: list[dict],
    starts: dict[str, date],
    ends: dict[str, date],
    duration_days: int,
) -> tuple[date | None, date | None]:
    """Fan-in: satisfy all predecessor links; start = latest required start."""
    dur = max(int(duration_days or 1), 1)
    start_candidates: list[date] = []
    end_candidates: list[date] = []

    for p in preds:
        ref = str(p.get("predecessor_ref") or "").strip()
        if not ref:
            continue
        link = parse_predecessor_link_type(p.get("link_type"))
        try:
            lag = max(int(p.get("lag_days") or 0), 0)
        except (TypeError, ValueError):
            lag = 0
        span = resolve_successor_span(
            link,
            starts.get(ref),
            ends.get(ref),
            dur,
            db,
            lag_days=lag,
        )
        if not span[0] or not span[1]:
            return None, None
        s, e = span
        start_candidates.append(s)
        if link in (PredecessorLinkType.FF, PredecessorLinkType.SF):
            end_candidates.append(e)

    if not start_candidates:
        return None, None

    start = max(start_candidates)
    end = add_business_days(start, dur, db)
    if end_candidates:
        end = max([end, *end_candidates])
        start = subtract_business_days_from_end(db, end, dur)
    return start, end


def _preds_ready(preds: list[dict], starts: dict[str, date], ends: dict[str, date]) -> bool:
    for p in preds:
        ref = str(p.get("predecessor_ref") or "").strip()
        if not ref:
            continue
        link = parse_predecessor_link_type(p.get("link_type"))
        if link_type_requires_pred_end(link) and ref not in ends:
            return False
        if link_type_requires_pred_start(link) and ref not in starts:
            return False
    return True


def _default_start_from_siblings(
    db: Session | None,
    row: dict,
    sched: list[dict],
    ends: dict[str, date],
    by_ref: dict[str, dict],
    project_start: date,
) -> date:
    parent_ref = str(row.get("parent_ref") or "").strip() or None
    parent_id = row.get("parent_id")
    siblings = sorted(
        [
            s
            for s in sched
            if (s.get("parent_ref") or None) == parent_ref
            and (parent_ref is not None or not s.get("parent_ref"))
        ],
        key=lambda x: (int(x.get("sort_order") or 0), str(_row_ref(x))),
    )
    my_ref = _row_ref(row)
    idx = next((i for i, s in enumerate(siblings) if _row_ref(s) == my_ref), 0)
    for j in range(idx - 1, -1, -1):
        prev = siblings[j]
        pe = ends.get(_row_ref(prev))
        if pe:
            return business_day_after(pe, db)
    if parent_ref and parent_ref in by_ref:
        par = by_ref[parent_ref]
        ps = _parse_optional_date(par.get("start_date"))
        if ps:
            return add_business_days(ps, 1, db)
    return add_business_days(project_start, 1, db)


def _ref_depth(ref: str, by_ref: dict[str, dict], memo: dict[str, int]) -> int:
    if ref in memo:
        return memo[ref]
    row = by_ref.get(ref)
    if not row:
        memo[ref] = 0
        return 0
    parent = str(row.get("parent_ref") or "").strip()
    if not parent:
        memo[ref] = 0
        return 0
    memo[ref] = _ref_depth(parent, by_ref, memo) + 1
    return memo[ref]


def _rollup_work_children(raw: dict) -> bool:
    """Milestone gate tidak mendefinisikan span kerja parent — hanya task/phase/subtask."""
    return str(raw.get("item_type") or "").lower() in ("phase", "task", "subtask")


def _rollup_editor_dict_rows(working: list[dict], db: Session | None) -> None:
    by_ref = {_row_ref(r): r for r in working if _row_ref(r)}
    for ref in sorted(by_ref.keys(), key=lambda k: -_ref_depth(k, by_ref, {})):
        row = by_ref[ref]
        kids = [
            r
            for r in working
            if str(r.get("parent_ref") or "").strip() == ref and _rollup_work_children(r)
        ]
        if not kids:
            continue
        starts = [_parse_optional_date(c.get("start_date")) for c in kids]
        ends = [_parse_optional_date(c.get("target_date")) for c in kids]
        starts = [s for s in starts if s]
        ends = [e for e in ends if e]
        if not starts or not ends:
            continue
        start_d, end_d = min(starts), max(ends)
        row["start_date"] = start_d.isoformat()
        row["target_date"] = end_d.isoformat()
        row["duration_days"] = count_business_days_inclusive(start_d, end_d, db)


def _reschedule_rows_with_predecessors(
    db: Session | None,
    sched: list[dict],
    working: list[dict],
    starts: dict[str, date],
    ends: dict[str, date],
) -> None:
    """
    After phase rollup, predecessor ends may have moved (e.g. Development → UAT FS).
    Re-apply predecessor constraints until stable.
    """
    max_passes = max(len(sched) * 2, 4)
    for _ in range(max_passes):
        changed = False
        for row in sched:
            preds = normalize_predecessors(row)
            if not preds or not _preds_ready(preds, starts, ends):
                continue
            if str(row.get("item_type") or "phase").lower() == "milestone":
                continue
            try:
                dur = max(int(row.get("duration_days") or 1), 1)
            except (TypeError, ValueError):
                dur = 1
            start_d, end_d = _merge_multi_predecessor_span(db, preds, starts, ends, dur)
            if not start_d or not end_d:
                continue
            new_s, new_e = start_d.isoformat(), end_d.isoformat()
            if row.get("start_date") == new_s and row.get("target_date") == new_e:
                continue
            row["start_date"] = new_s
            row["target_date"] = new_e
            row["duration_days"] = count_business_days_inclusive(start_d, end_d, db)
            _register_span(starts, ends, row)
            changed = True
        _rollup_editor_dict_rows(working, db)
        for r in working:
            if str(r.get("item_type") or "").lower() in ("phase", "task", "subtask"):
                _register_span(starts, ends, r)
        if not changed:
            break


def _default_milestone_gate_date(
    row: dict,
    working: list[dict],
    by_ref: dict[str, dict],
    project_start: date,
) -> date:
    """Gate milestone: default = akhir pekerjaan sibling (max target) atau akhir parent phase."""
    my_key = _row_ref(row)
    parent_ref = str(row.get("parent_ref") or "").strip()
    sibling_ends: list[date] = []
    for raw in working:
        if _row_ref(raw) == my_key:
            continue
        if str(raw.get("parent_ref") or "").strip() != parent_ref:
            continue
        if str(raw.get("item_type") or "").lower() == "milestone":
            continue
        end_d = _parse_optional_date(raw.get("target_date"))
        if end_d:
            sibling_ends.append(end_d)
    if sibling_ends:
        return max(sibling_ends)
    if parent_ref and parent_ref in by_ref:
        pt = _parse_optional_date(by_ref[parent_ref].get("target_date"))
        if pt:
            return pt
    return project_start


def _milestone_gate_date(
    row: dict,
    working: list[dict],
    by_ref: dict[str, dict],
    project_start: date,
) -> date:
    default_day = _default_milestone_gate_date(row, working, by_ref, project_start)
    driver = str(row.get("schedule_driver") or "").strip().lower()
    manual = _parse_optional_date(row.get("target_date"))
    if manual and driver in ("end", "milestone", "manual"):
        return manual
    return default_day


def _register_span(
    starts: dict[str, date],
    ends: dict[str, date],
    row: dict,
) -> None:
    start_d = _parse_optional_date(row.get("start_date"))
    end_d = _parse_optional_date(row.get("target_date"))
    if not start_d or not end_d:
        return
    rk = _row_ref(row)
    starts[rk] = start_d
    ends[rk] = end_d
    if row.get("row_key"):
        rid = row.get("id")
        if rid is not None:
            starts[str(rid)] = start_d
            ends[str(rid)] = end_d


def recalc_timeline_editor_rows(
    db: Session | None,
    rows: list[dict],
    project_start: date,
) -> list[dict]:
    """
    Auto-calc start/end from duration, business calendar, and multi-predecessor links.
    Returns new row dicts (does not mutate DB).
    """
    working = [dict(r) for r in rows]
    for raw in working:
        apply_schedule_driver_to_raw(db, raw)

    by_ref = {_row_ref(r): r for r in working if _row_ref(r)}
    sched = [
        r
        for r in working
        if str(r.get("item_type") or "phase").lower()
        in ("phase", "task", "subtask")
    ]

    starts: dict[str, date] = {}
    ends: dict[str, date] = {}
    pending = sorted(sched, key=lambda x: (int(x.get("sort_order") or 0), _row_ref(x)))
    guard = 0

    while pending and guard < len(pending) * 6 + 24:
        guard += 1
        row = pending[0]
        preds = normalize_predecessors(row)
        parent_ref = str(row.get("parent_ref") or "").strip()
        if parent_ref and parent_ref in by_ref:
            par = by_ref[parent_ref]
            if str(par.get("item_type") or "").lower() != "milestone" and parent_ref not in starts:
                pending.append(pending.pop(0))
                continue

        if preds and not _preds_ready(preds, starts, ends):
            pending.append(pending.pop(0))
            continue

        it = str(row.get("item_type") or "phase").lower()
        if it == "milestone":
            pending.pop(0)
            continue

        try:
            dur = max(int(row.get("duration_days") or 1), 1)
        except (TypeError, ValueError):
            dur = 1

        start_d: date | None = None
        end_d: date | None = None

        if preds:
            start_d, end_d = _merge_multi_predecessor_span(db, preds, starts, ends, dur)

        driver = str(row.get("schedule_driver") or "").strip().lower()
        manual_start = _parse_optional_date(row.get("start_date"))
        manual_end = _parse_optional_date(row.get("target_date"))

        if start_d is None and not preds and manual_start and driver in ("start", "duration", ""):
            start_d = add_business_days(manual_start, 1, db)
            end_d = add_business_days(start_d, dur, db)
        if start_d and end_d is None and driver == "duration" and not preds:
            end_d = add_business_days(start_d, dur, db)
        if start_d and manual_end and driver == "end" and not preds:
            end_d = add_business_days(manual_end, 1, db)
            start_d = subtract_business_days_from_end(db, end_d, dur)

        if start_d is None or end_d is None:
            start_d = _default_start_from_siblings(db, row, sched, ends, by_ref, project_start)
            end_d = add_business_days(start_d, dur, db)

        row["start_date"] = start_d.isoformat()
        row["target_date"] = end_d.isoformat()
        row["duration_days"] = count_business_days_inclusive(start_d, end_d, db)
        _register_span(starts, ends, row)
        pending.pop(0)

    _rollup_editor_dict_rows(working, db)
    for row in working:
        if str(row.get("item_type") or "").lower() in ("phase", "task", "subtask"):
            _register_span(starts, ends, row)

    _reschedule_rows_with_predecessors(db, sched, working, starts, ends)

    for row in working:
        if str(row.get("item_type") or "").lower() != "milestone":
            continue
        d = _milestone_gate_date(row, working, by_ref, project_start)
        row["start_date"] = d.isoformat()
        row["target_date"] = d.isoformat()
        row["duration_days"] = 0

    return working


def recalc_timeline_editor_payload(
    db: Session | None,
    rows: list[dict],
    project_start: date | None,
) -> dict[str, Any]:
    if not project_start:
        raise ValueError("start_date wajib untuk hitung ulang timeline editor")
    out = recalc_timeline_editor_rows(db, rows, project_start)
    return {
        "rows": out,
        "project_timeline": compute_project_timeline_summary(db, out, project_start),
    }
