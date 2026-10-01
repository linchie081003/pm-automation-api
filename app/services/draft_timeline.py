from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Project, ScheduleBaselineMilestone, TimelineItemType
from app.services.project_lifecycle import draft_timeline_editable
from app.services.schedule import get_draft_baseline, get_or_revive_sph_draft_baseline
from app.services.timeline_item_type import parse_timeline_item_type
from app.services.timeline_schedule import (
    apply_schedule_driver_to_raw,
    compute_project_timeline_summary,
    schedule_draft_milestone_rows,
)
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
        "predecessor_ref": r.predecessor_ref,
        "predecessor_link_type": (r.predecessor_link_type or "FS").upper()
        if r.predecessor_ref
        else None,
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


def recalc_draft_dates(
    db: Session,
    project_id: int,
    start: date,
    milestone_manual: dict[int, date | None] | None = None,
    row_inputs: dict[int, dict] | None = None,
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
    schedule_draft_milestone_rows(db, rows, start, milestone_manual, row_inputs)


def preview_recalc_draft_rows(
    db: Session,
    project: Project,
    rows: list[dict],
    start: date | None,
) -> list[dict]:
    """Recalc dates via kalender kerja without committing (savepoint rollback)."""
    sp = db.begin_nested()
    try:
        out = save_draft_rows(db, project, rows, start)
        sp.rollback()
        return out
    except Exception:
        sp.rollback()
        raise


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
    for raw in rows:
        apply_schedule_driver_to_raw(db, raw)

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
    inputs_by_id: dict[int, dict] = {}
    normalized = sorted(rows, key=lambda x: int(x.get("sort_order") or 0))
    for raw in normalized:
        parent_ref = raw.get("parent_ref") or raw.get("parent_row_key")
        parent_id = id_map.get(str(parent_ref)) if parent_ref else None
        it = parse_timeline_item_type(str(raw.get("item_type") or "phase"))
        row_key = str(raw.get("row_key") or raw.get("id") or "")
        dur = int(raw["duration_days"]) if raw.get("duration_days") is not None else 1
        if it == TimelineItemType.milestone:
            dur = 0
        pred_ref = str(raw.get("predecessor_ref") or "").strip() or None
        link_raw = str(raw.get("predecessor_link_type") or "FS").strip().upper()
        pred_link = link_raw if pred_ref and link_raw in ("FS", "SS", "FF", "SF") else None
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
            predecessor_ref=pred_ref,
            predecessor_link_type=pred_link or ("FS" if pred_ref else None),
        )
        db.add(r)
        db.flush()
        inputs_by_id[r.id] = raw
        if it == TimelineItemType.milestone:
            milestone_manual[r.id] = _parse_optional_date(raw.get("target_date"))
        if row_key:
            id_map[row_key] = r.id
        id_map[str(r.id)] = r.id
    eff = start or draft.effective_from or date.today()
    recalc_draft_dates(db, project.id, eff, milestone_manual, inputs_by_id)
    return list_draft_rows(db, project.id)


def draft_project_timeline_summary(
    db: Session,
    project_id: int,
    project_start: date | None,
) -> dict:
    rows = list_draft_rows(db, project_id)
    return compute_project_timeline_summary(db, rows, project_start)
