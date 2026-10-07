from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Milestone,
    MilestoneLivePredecessor,
    MilestonePredecessor,
    MilestoneStatus,
    Project,
    ProjectPo,
    ScheduleBaselineMilestone,
)
from app.services.draft_timeline import list_draft_rows
from app.services.schedule import get_draft_baseline
from app.core.timezone import now_jakarta


def _load_draft_predecessors(db: Session, row_ids: list[int]) -> dict[int, list[dict]]:
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


def list_draft_timeline(db: Session, project_id: int) -> list[dict]:
    """Draft untuk tab Kick Off — hanya setelah SPH selesai (lanjut Kick Off)."""
    from app.models import ProjectSph

    sph = db.get(ProjectSph, project_id)
    if not sph or not sph.draft_baseline_generated_at:
        return []
    rows = list_draft_rows(db, project_id)
    return rows


def confirm_kickoff_timeline(db: Session, project: Project) -> int:
    if project.kickoff_timeline_confirmed_at:
        raise ValueError("Timeline kick off sudah dikonfirmasi")
    draft = get_draft_baseline(db, project.id)
    if not draft:
        raise ValueError("Draft timeline belum digenerate dari SPH")
    rows = list(
        db.scalars(
            select(ScheduleBaselineMilestone).where(
                ScheduleBaselineMilestone.baseline_id == draft.id
            )
        ).all()
    )
    if not rows:
        raise ValueError("Draft timeline kosong")
    from app.services.timeline_validation import validate_timeline_items

    def _baseline_row_dict(r: ScheduleBaselineMilestone) -> dict:
        return {
            "id": r.id,
            "row_key": r.row_key or str(r.id),
            "name": r.name,
            "item_type": r.item_type.value if r.item_type else "phase",
            "weight_pct": r.weight_pct,
            "parent_id": r.parent_id,
        }

    id_to_row = {r.id: _baseline_row_dict(r) for r in rows}
    items = []
    for r in rows:
        parent_ref = None
        if r.parent_id and r.parent_id in id_to_row:
            pr = id_to_row[r.parent_id]
            parent_ref = pr.get("row_key")
        items.append(
            {
                "row_key": r.row_key or str(r.id),
                "name": r.name,
                "item_type": r.item_type.value if r.item_type else "phase",
                "weight_pct": r.weight_pct,
                "parent_key": parent_ref,
                "duration_days": r.duration_days or 1,
            }
        )
    validate_timeline_items(items)
    preds_by_draft_row = _load_draft_predecessors(db, [r.id for r in rows])
    db.execute(delete(Milestone).where(Milestone.project_id == project.id))
    id_map: dict[int, int] = {}
    pending = list(rows)
    while pending:
        progress = False
        next_pending: list[ScheduleBaselineMilestone] = []
        for r in pending:
            if r.parent_id and r.parent_id not in id_map:
                next_pending.append(r)
                continue
            m = Milestone(
                project_id=project.id,
                name=r.name,
                start_date=r.start_date,
                target_date=r.target_date,
                weight_pct=r.weight_pct,
                is_payment_milestone=r.is_payment_milestone,
                duration_days=r.duration_days,
                item_type=r.item_type,
                parent_id=id_map.get(r.parent_id) if r.parent_id else None,
                sort_order=r.sort_order,
                status=MilestoneStatus.open,
            )
            db.add(m)
            db.flush()
            id_map[r.id] = m.id
            progress = True
        if not progress:
            break
        pending = next_pending

    row_key_to_live: dict[str, int] = {}
    for dr in rows:
        live_id = id_map.get(dr.id)
        if live_id is None:
            continue
        if dr.row_key:
            row_key_to_live[str(dr.row_key)] = live_id
        row_key_to_live[str(dr.id)] = live_id
        row_key_to_live[f"m:{live_id}"] = live_id

    for dr in rows:
        live_id = id_map.get(dr.id)
        if live_id is None:
            continue
        preds = preds_by_draft_row.get(dr.id) or []
        if not preds and dr.predecessor_ref:
            preds = [
                {
                    "predecessor_ref": dr.predecessor_ref,
                    "link_type": (dr.predecessor_link_type or "FS").upper(),
                    "lag_days": 0,
                }
            ]
        for i, p in enumerate(preds):
            pref = str(p.get("predecessor_ref") or "").strip()
            if not pref:
                continue
            resolved = row_key_to_live.get(pref)
            stored_ref = f"m:{resolved}" if resolved is not None else pref
            db.add(
                MilestoneLivePredecessor(
                    milestone_id=live_id,
                    predecessor_ref=stored_ref,
                    link_type=str(p.get("link_type") or "FS").strip().upper() or "FS",
                    lag_days=max(int(p.get("lag_days") or 0), 0),
                    sort_order=i,
                )
            )

    confirmed_at = now_jakarta()
    project.kickoff_timeline_confirmed_at = confirmed_at
    live_dates = [m.start_date for m in db.scalars(
        select(Milestone).where(Milestone.project_id == project.id)
    ).all() if m.start_date]
    project.planned_start_date = min(live_dates) if live_dates else confirmed_at.date()
    po = db.get(ProjectPo, project.id)
    if po and po.po_due_date:
        project.planned_end_date = po.po_due_date
        project.po_due_date = po.po_due_date
    else:
        live = list(db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all())
        targets = [m.target_date for m in live if m.target_date]
        if targets:
            project.planned_end_date = max(targets)
            if not project.po_due_date:
                project.po_due_date = project.planned_end_date
    return len(rows)
