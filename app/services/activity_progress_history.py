"""Daily activity progress snapshots (frozen for weekly reports as-of cut-off)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import today_jakarta
from app.models import ActivityHistorySource, ActivityProgressHistory, Project
from app.services.timeline_display import timeline_display_rows_for_project
from app.services.timeline_report_metrics import (
    build_report_row_progress_context,
    display_row_actual_pct,
)


def _parse_iso(val: object) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def record_activity_snapshot(db: Session, project: Project) -> None:
    """Dipanggil tiap sync ClickUp. Upsert 1 baris per milestone per hari (dedup harian)."""
    rows = timeline_display_rows_for_project(db, project.id, include_subtasks=False)
    today = today_jakarta()
    ctx = build_report_row_progress_context(db, project)
    for row in rows:
        mid = row.get("id")
        if not isinstance(mid, int) or mid <= 0:
            continue
        pct = display_row_actual_pct(ctx, row, today)
        existing = db.scalar(
            select(ActivityProgressHistory).where(
                ActivityProgressHistory.milestone_id == mid,
                ActivityProgressHistory.captured_at == today,
            )
        )
        values = dict(
            status=row.get("status") or "open",
            progress_pct=pct,
            start_date=_parse_iso(row.get("display_start") or row.get("start_date")),
            end_date=_parse_iso(row.get("display_end") or row.get("target_date")),
        )
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            db.add(
                ActivityProgressHistory(
                    project_id=project.id,
                    milestone_id=mid,
                    captured_at=today,
                    source=ActivityHistorySource.clickup_sync,
                    **values,
                )
            )


def get_asof_rows(
    db: Session,
    project_id: int,
    as_of: date,
    *,
    include_subtasks: bool = False,
) -> list[dict]:
    """Baris timeline untuk report: baca snapshot terdekat <= as_of per milestone."""
    live_rows = timeline_display_rows_for_project(
        db, project_id, include_subtasks=include_subtasks
    )
    ids = [r["id"] for r in live_rows if isinstance(r.get("id"), int) and r["id"] > 0]
    snap_by_milestone: dict[int, ActivityProgressHistory] = {}
    if ids:
        latest_per_milestone = db.scalars(
            select(ActivityProgressHistory)
            .where(
                ActivityProgressHistory.milestone_id.in_(ids),
                ActivityProgressHistory.captured_at <= as_of,
            )
            .order_by(
                ActivityProgressHistory.milestone_id,
                ActivityProgressHistory.captured_at.desc(),
            )
        ).all()
        for h in latest_per_milestone:
            snap_by_milestone.setdefault(h.milestone_id, h)

    out: list[dict] = []
    for row in live_rows:
        snap = snap_by_milestone.get(row.get("id"))
        if snap:
            row = dict(row)
            row["status"] = snap.status
            row["clickup_progress_pct"] = snap.progress_pct
            if snap.start_date:
                row["display_start"] = snap.start_date.isoformat()
            if snap.end_date:
                row["display_end"] = snap.end_date.isoformat()
        out.append(row)
    return out
