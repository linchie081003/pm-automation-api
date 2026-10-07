from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import MilestonePredecessor, Project, ScheduleBaselineMilestone, TimelineItemType
from app.services.milestone_predecessor_sync import sync_baseline_milestone_predecessors
from app.services.project_lifecycle import draft_timeline_editable
from app.services.schedule import get_draft_baseline, get_or_revive_sph_draft_baseline
from app.services.timeline_engine_config import use_timeline_engine_v2
from app.services.timeline_item_type import parse_timeline_item_type
from app.services.timeline_recalc_adapter import recalc_draft_dict_rows
from app.services.timeline_editor_engine import normalize_predecessors
from app.services.timeline_schedule import (
    apply_schedule_driver_to_raw,
    compute_project_timeline_summary,
    infer_milestone_schedule_driver,
    schedule_draft_milestone_rows,
)
from app.services.timeline_validation import validate_timeline_items
from app.core.timezone import today_jakarta


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


def _load_draft_predecessors_by_row_id(db: Session, baseline_id: int) -> dict[int, list[dict]]:
    row_ids = list(
        db.scalars(
            select(ScheduleBaselineMilestone.id).where(
                ScheduleBaselineMilestone.baseline_id == baseline_id
            )
        ).all()
    )
    if not row_ids:
        return {}
    links = db.scalars(
        select(MilestonePredecessor)
        .where(MilestonePredecessor.milestone_row_id.in_(row_ids))
        .order_by(MilestonePredecessor.milestone_row_id, MilestonePredecessor.sort_order)
    ).all()
    out: dict[int, list[dict]] = {}
    for link in links:
        out.setdefault(link.milestone_row_id, []).append(
            {
                "predecessor_ref": link.predecessor_ref,
                "link_type": (link.link_type or "FS").upper(),
                "lag_days": int(link.lag_days or 0),
            }
        )
    return out


def _row_out(
    r: ScheduleBaselineMilestone,
    id_to_row_key: dict[int, str] | None = None,
    preds_by_row: dict[int, list[dict]] | None = None,
) -> dict:
    parent_ref = None
    if r.parent_id and id_to_row_key:
        parent_ref = id_to_row_key.get(r.parent_id)
    preds = (preds_by_row or {}).get(r.id, [])
    first = preds[0] if preds else None
    out = {
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
        "predecessor_ref": r.predecessor_ref or (first.get("predecessor_ref") if first else None),
        "predecessor_link_type": (
            (r.predecessor_link_type or "FS").upper()
            if r.predecessor_ref
            else (first.get("link_type") if first else None)
        ),
        "schedule_driver": getattr(r, "schedule_driver", None),
        "predecessors": preds if preds else [],
    }
    if not out["predecessors"] and (out["predecessor_ref"] or out.get("predecessor_link_type")):
        out["predecessors"] = normalize_predecessors(out)
    infer_milestone_schedule_driver(out)
    return out


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
    preds_by_row = _load_draft_predecessors_by_row_id(db, draft.id)
    id_to_row_key: dict[int, str] = {}
    for r in rows:
        if r.id is not None and r.row_key:
            id_to_row_key[r.id] = r.row_key
    return [_row_out(r, id_to_row_key, preds_by_row) for r in rows]


def _resolve_draft_row_parents(rows: list[dict]) -> list[dict]:
    id_to_key: dict[int, str] = {}
    for raw in rows:
        rk = str(raw.get("row_key") or raw.get("id") or "").strip()
        if raw.get("id") is not None:
            try:
                id_to_key[int(raw["id"])] = rk or id_to_key.get(int(raw["id"]), "")
            except (TypeError, ValueError):
                pass
        if rk:
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
        if not r.get("predecessors"):
            r["predecessors"] = normalize_predecessors(r)
        out.append(r)
    return out


def _validate_draft_row_items(rows: list[dict]) -> None:
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
                "row_key": str(
                    raw.get("row_key") or raw.get("id") or f"row_{raw.get('sort_order')}"
                ),
                "name": raw.get("name"),
                "item_type": it,
                "weight_pct": raw.get("weight_pct") or 0,
                "parent_key": raw.get("parent_ref") or raw.get("parent_row_key"),
                "duration_days": dur,
            }
        )
    validate_timeline_items(validate_items)


def _persist_draft_rows_from_dicts(
    db: Session,
    draft,
    recalced: list[dict],
) -> list[dict]:
    row_ids = list(
        db.scalars(
            select(ScheduleBaselineMilestone.id).where(
                ScheduleBaselineMilestone.baseline_id == draft.id
            )
        ).all()
    )
    if row_ids:
        db.execute(
            delete(MilestonePredecessor).where(
                MilestonePredecessor.milestone_row_id.in_(row_ids)
            )
        )
    db.execute(
        delete(ScheduleBaselineMilestone).where(
            ScheduleBaselineMilestone.baseline_id == draft.id
        )
    )
    db.flush()

    id_map: dict[str, int] = {}
    saved_meta: list[dict] = []
    normalized = sorted(recalced, key=lambda x: int(x.get("sort_order") or 0))

    for i, raw in enumerate(normalized):
        parent_ref = raw.get("parent_ref") or raw.get("parent_row_key")
        parent_id = id_map.get(str(parent_ref)) if parent_ref else None
        it = parse_timeline_item_type(str(raw.get("item_type") or "phase"))
        row_key = str(raw.get("row_key") or raw.get("id") or f"row_{i}").strip()
        dur = int(raw["duration_days"]) if raw.get("duration_days") is not None else 1
        if it == TimelineItemType.milestone:
            dur = 0
        preds = normalize_predecessors(raw)
        first = preds[0] if preds else None
        pred_ref = str(raw.get("predecessor_ref") or "").strip() or (
            first.get("predecessor_ref") if first else None
        )
        link_raw = str(
            raw.get("predecessor_link_type") or (first.get("link_type") if first else "FS")
        ).strip().upper()
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
            sort_order=int(raw.get("sort_order") if raw.get("sort_order") is not None else i),
            start_date=_parse_optional_date(raw.get("start_date")),
            target_date=_parse_optional_date(raw.get("target_date")),
            predecessor_ref=pred_ref,
            predecessor_link_type=pred_link or ("FS" if pred_ref else None),
            schedule_driver=str(raw.get("schedule_driver") or "").strip() or None,
        )
        db.add(r)
        db.flush()
        if row_key:
            id_map[row_key] = r.id
        id_map[str(r.id)] = r.id
        saved_meta.append(
            {
                "id": r.id,
                "row_key": row_key or str(r.id),
            }
        )

    sync_baseline_milestone_predecessors(db, saved_meta, recalced)
    return list_draft_rows(db, draft.project_id)


def recalc_draft_dates(
    db: Session,
    project_id: int,
    start: date,
    milestone_manual: dict[int, date | None] | None = None,
    row_inputs: dict[int, dict] | None = None,
) -> None:
    if use_timeline_engine_v2():
        draft = get_draft_baseline(db, project_id)
        if not draft:
            return
        rows = list_draft_rows(db, project_id)
        if not rows:
            return
        recalced = recalc_draft_dict_rows(db, rows, start)
        _persist_draft_rows_from_dicts(db, draft, recalced)
        return

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
    if use_timeline_engine_v2():
        if not start:
            raise ValueError("start_date wajib untuk recalc draft timeline")
        rows = _resolve_draft_row_parents(rows)
        for raw in rows:
            apply_schedule_driver_to_raw(db, raw)
        _validate_draft_row_items(rows)
        return recalc_draft_dict_rows(db, rows, start)

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
    _validate_draft_row_items(rows)

    if start:
        draft.effective_from = start
    eff = start or draft.effective_from or today_jakarta()

    if use_timeline_engine_v2():
        recalced = recalc_draft_dict_rows(db, rows, eff)
        return _persist_draft_rows_from_dicts(db, draft, recalced)

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
    recalc_draft_dates(db, project.id, eff, milestone_manual, inputs_by_id)
    return list_draft_rows(db, project.id)


def draft_project_timeline_summary(
    db: Session,
    project_id: int,
    project_start: date | None,
) -> dict:
    rows = list_draft_rows(db, project_id)
    return compute_project_timeline_summary(db, rows, project_start)
