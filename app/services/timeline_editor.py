"""Timeline Editor (beta): read-only snapshot from SPH draft + optional stored multi-predecessors."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MilestonePredecessor, Project, ScheduleBaselineMilestone
from app.services.draft_timeline import list_draft_rows
from app.services.sph import get_or_create_sph
from app.services.schedule import get_draft_baseline, get_or_revive_sph_draft_baseline
from app.services.timeline_editor_engine import normalize_predecessors, recalc_timeline_editor_payload


def _parse_start(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _load_predecessors_by_row_id(db: Session, baseline_id: int) -> dict[int, list[dict]]:
    rows = db.scalars(
        select(ScheduleBaselineMilestone.id).where(
            ScheduleBaselineMilestone.baseline_id == baseline_id
        )
    ).all()
    if not rows:
        return {}
    row_ids = list(rows)
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


def timeline_editor_snapshot(db: Session, project_id: int) -> dict:
    """Copy of draft baseline rows for sandbox UI (does not touch live milestones)."""
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    sph = get_or_create_sph(db, project_id)
    draft = get_draft_baseline(db, project_id) or get_or_revive_sph_draft_baseline(
        db, project_id
    )
    preds_by_row: dict[int, list[dict]] = {}
    if draft:
        preds_by_row = _load_predecessors_by_row_id(db, draft.id)

    rows = list_draft_rows(db, project_id)
    enriched: list[dict] = []
    for row in rows:
        copy = dict(row)
        rid = copy.get("id")
        if isinstance(rid, int) and rid in preds_by_row:
            copy["predecessors"] = preds_by_row[rid]
        else:
            copy["predecessors"] = normalize_predecessors(copy)
        enriched.append(copy)

    start = _parse_start(sph.estimated_start_date)
    return {
        "project_id": project_id,
        "start_date": start.isoformat() if start else None,
        "rows": enriched,
        "read_only_source": "schedule_baseline_draft",
        "note": "Sandbox beta — perubahan di sini tidak menulis tab SPH/Timeline live kecuali nanti via Apply.",
    }


def timeline_editor_recalc(
    db: Session,
    project_id: int,
    rows: list[dict],
    start_date: date | None,
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    sph = get_or_create_sph(db, project_id)
    eff = start_date or _parse_start(sph.estimated_start_date)
    if not eff:
        raise ValueError("Isi tanggal mulai proyek (estimasi start) terlebih dahulu")
    payload = recalc_timeline_editor_payload(db, rows, eff)
    payload["project_id"] = project_id
    payload["start_date"] = eff.isoformat()
    return payload
