from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Milestone, MilestoneStatus, Project, ProjectPo, ScheduleBaselineMilestone
from app.services.draft_timeline import list_draft_rows
from app.services.schedule import get_draft_baseline
from app.core.timezone import now_jakarta


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
