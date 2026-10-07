"""Sync multi-predecessor links on schedule_baseline_milestone rows."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import MilestonePredecessor
from app.services.timeline_editor_engine import normalize_predecessors


def sync_baseline_milestone_predecessors(
    db: Session,
    saved_rows: list[dict],
    source_rows: list[dict],
) -> None:
    """Replace milestone_predecessors for draft baseline rows from recalc/source dicts."""
    pred_by_key = {str(r.get("row_key") or r.get("id") or ""): normalize_predecessors(r) for r in source_rows}
    ids = [int(r["id"]) for r in saved_rows if r.get("id") is not None]
    if ids:
        db.execute(
            delete(MilestonePredecessor).where(
                MilestonePredecessor.milestone_row_id.in_(ids)
            )
        )
    for saved in saved_rows:
        sid = saved.get("id")
        if sid is None:
            continue
        rk = str(saved.get("row_key") or sid)
        preds = pred_by_key.get(rk) or pred_by_key.get(str(sid)) or []
        for i, p in enumerate(preds):
            pref = str(p.get("predecessor_ref") or "").strip()
            if not pref:
                continue
            db.add(
                MilestonePredecessor(
                    milestone_row_id=int(sid),
                    predecessor_ref=pref,
                    link_type=str(p.get("link_type") or "FS").strip().upper() or "FS",
                    lag_days=max(int(p.get("lag_days") or 0), 0),
                    sort_order=i,
                )
            )
