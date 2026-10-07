"""Persist Timeline Editor (beta) rows in dedicated tables (not SPH draft baseline)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.timezone import now_jakarta
from app.models import (
    Project,
    ProjectStatus,
    TimelineEditorPredecessor,
    TimelineEditorRow,
    TimelineEditorState,
    TimelineItemType,
)
from app.services.draft_timeline import list_draft_rows
from app.services.timeline_editor_engine import normalize_predecessors, validate_timeline_predecessors
from app.services.timeline_schedule import infer_milestone_schedule_driver
from app.services.timeline_item_type import parse_timeline_item_type
from app.services.timeline_validation import validate_timeline_items


def _parse_optional_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def editor_has_rows(db: Session, project_id: int) -> bool:
    row = db.scalar(
        select(TimelineEditorRow.id)
        .where(TimelineEditorRow.project_id == project_id)
        .limit(1)
    )
    return row is not None


def get_editor_start_date(db: Session, project_id: int) -> date | None:
    st = db.get(TimelineEditorState, project_id)
    return st.start_date if st else None


def _row_to_dict(
    r: TimelineEditorRow,
    id_to_row_key: dict[int, str],
    preds: list[dict],
) -> dict:
    parent_ref = id_to_row_key.get(r.parent_id) if r.parent_id else None
    first = preds[0] if preds else None
    out = {
        "id": r.id,
        "row_key": r.row_key,
        "name": r.name,
        "start_date": r.start_date.isoformat() if r.start_date else None,
        "target_date": r.target_date.isoformat() if r.target_date else None,
        "duration_days": r.duration_days,
        "weight_pct": r.weight_pct,
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
        "schedule_driver": r.schedule_driver,
        "predecessors": preds,
    }
    infer_milestone_schedule_driver(out)
    return out


def list_editor_rows(db: Session, project_id: int) -> list[dict]:
    rows = list(
        db.scalars(
            select(TimelineEditorRow)
            .where(TimelineEditorRow.project_id == project_id)
            .order_by(TimelineEditorRow.sort_order, TimelineEditorRow.id)
        ).all()
    )
    if not rows:
        return []
    row_ids = [r.id for r in rows]
    links = db.scalars(
        select(TimelineEditorPredecessor)
        .where(TimelineEditorPredecessor.editor_row_id.in_(row_ids))
        .order_by(
            TimelineEditorPredecessor.editor_row_id,
            TimelineEditorPredecessor.sort_order,
        )
    ).all()
    preds_by_row: dict[int, list[dict]] = {}
    for link in links:
        preds_by_row.setdefault(link.editor_row_id, []).append(
            {
                "predecessor_ref": link.predecessor_ref,
                "link_type": (link.link_type or "FS").upper(),
                "lag_days": int(link.lag_days or 0),
            }
        )
    id_to_row_key: dict[int, str] = {}
    for r in rows:
        if r.id is not None and r.row_key:
            id_to_row_key[r.id] = r.row_key
    return [
        _row_to_dict(r, id_to_row_key, preds_by_row.get(r.id, [])) for r in rows
    ]


def _load_draft_predecessors_by_row_id(db: Session, baseline_id: int) -> dict[int, list[dict]]:
    from app.models import MilestonePredecessor, ScheduleBaselineMilestone

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


def seed_rows_from_draft(db: Session, project_id: int) -> list[dict]:
    """Read-only copy from SPH draft for first open (not persisted until Simpan)."""
    from app.services.schedule import get_draft_baseline, get_or_revive_sph_draft_baseline

    draft_rows = list_draft_rows(db, project_id)
    if not draft_rows:
        return []
    preds_by_row: dict[int, list[dict]] = {}
    bl = get_draft_baseline(db, project_id) or get_or_revive_sph_draft_baseline(
        db, project_id
    )
    if bl:
        preds_by_row = _load_draft_predecessors_by_row_id(db, bl.id)
    enriched: list[dict] = []
    for row in draft_rows:
        copy = dict(row)
        rid = copy.get("id")
        if isinstance(rid, int) and rid in preds_by_row:
            copy["predecessors"] = preds_by_row[rid]
        else:
            copy["predecessors"] = normalize_predecessors(copy)
        enriched.append(copy)
    return enriched


def assert_editor_save_allowed(project: Project) -> None:
    if project.status == ProjectStatus.closed:
        raise ValueError("Proyek sudah closed — workspace Timeline Editor tidak bisa disimpan.")


class EditorWorkspaceConflictError(ValueError):
    """Optimistic lock: workspace changed on server since client loaded snapshot."""


def _parse_workspace_version(raw: datetime | str | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("workspace_updated_at tidak valid (harus ISO datetime).") from None


def assert_workspace_version(
    db: Session,
    project_id: int,
    expected_updated_at: datetime | str | None,
) -> None:
    """Reject save if server workspace version differs (multi-tab / multi-user)."""
    expected = _parse_workspace_version(expected_updated_at)
    has_rows = editor_has_rows(db, project_id)
    st = db.get(TimelineEditorState, project_id)
    server_at = st.updated_at if st else None

    if has_rows:
        if expected is None:
            raise EditorWorkspaceConflictError(
                "Workspace editor sudah berisi data di server — muat ulang halaman "
                "sebelum menyimpan (menghindari menimpa perubahan orang lain)."
            )
        if server_at is None:
            raise EditorWorkspaceConflictError(
                "Versi workspace tidak tersedia di server — muat ulang lalu simpan lagi."
            )
        if server_at.replace(tzinfo=None) != expected.replace(tzinfo=None):
            raise EditorWorkspaceConflictError(
                "Konflik simpan: workspace editor sudah diubah (tab lain atau PM lain). "
                f"Versi server: {server_at.isoformat(timespec='seconds')}. "
                "Muat ulang, gabungkan perubahan manual jika perlu, lalu simpan lagi."
            )
    elif expected is not None:
        raise EditorWorkspaceConflictError(
            "Workspace editor di server kosong, tetapi browser masih membawa versi lama — "
            "muat ulang sebelum menyimpan."
        )


def workspace_updated_at_iso(db: Session, project_id: int) -> str | None:
    if not editor_has_rows(db, project_id):
        return None
    st = db.get(TimelineEditorState, project_id)
    if not st or not st.updated_at:
        return None
    return st.updated_at.isoformat()


def save_editor_rows(
    db: Session,
    project: Project,
    recalced: list[dict],
    start_date: date | None,
    *,
    expected_workspace_updated_at: datetime | str | None = None,
) -> list[dict]:
    assert_editor_save_allowed(project)
    project_id = project.id
    assert_workspace_version(db, project_id, expected_workspace_updated_at)
    rows = list(recalced)
    validate_timeline_predecessors(rows)
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

    row_ids = db.scalars(
        select(TimelineEditorRow.id).where(TimelineEditorRow.project_id == project_id)
    ).all()
    if row_ids:
        db.execute(
            delete(TimelineEditorPredecessor).where(
                TimelineEditorPredecessor.editor_row_id.in_(list(row_ids))
            )
        )
    db.execute(delete(TimelineEditorRow).where(TimelineEditorRow.project_id == project_id))
    db.flush()

    id_map: dict[str, int] = {}
    normalized = sorted(rows, key=lambda x: int(x.get("sort_order") or 0))
    saved_meta: list[tuple[int, str, list[dict]]] = []

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

        r = TimelineEditorRow(
            project_id=project_id,
            row_key=row_key or None,
            name=str(raw.get("name") or "").strip() or "Item",
            duration_days=dur,
            weight_pct=float(raw.get("weight_pct") or 0),
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
        saved_meta.append((r.id, row_key or str(r.id), preds))

    for editor_row_id, rk, preds in saved_meta:
        for j, p in enumerate(preds):
            pref = str(p.get("predecessor_ref") or "").strip()
            if not pref:
                continue
            db.add(
                TimelineEditorPredecessor(
                    editor_row_id=editor_row_id,
                    predecessor_ref=pref,
                    link_type=str(p.get("link_type") or "FS").strip().upper() or "FS",
                    lag_days=max(int(p.get("lag_days") or 0), 0),
                    sort_order=j,
                )
            )

    st = db.get(TimelineEditorState, project_id)
    if not st:
        st = TimelineEditorState(project_id=project_id)
        db.add(st)
    if start_date:
        st.start_date = start_date
    st.updated_at = now_jakarta()
    db.flush()
    return list_editor_rows(db, project_id)
