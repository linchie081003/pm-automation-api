"""Timeline / progress metrics for report generators (Excel, PPTX) — single engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Milestone, MilestoneStatus, Project
from app.services.business_calendar import count_business_days_inclusive
from app.services.schedule import phase_planned_progress_pct


def display_row_weight_fraction(row: dict) -> float:
    return float(row.get("weight_pct") or 0) / 100.0


def display_row_schedule_proxy(row: dict) -> SimpleNamespace:
    start = _parse_row_date(row.get("display_start") or row.get("start_date"))
    end = _parse_row_date(row.get("display_end") or row.get("target_date"))
    dur = row.get("display_duration_days") or row.get("duration_days")
    return SimpleNamespace(
        start_date=start,
        target_date=end,
        duration_days=dur,
        weight_pct=row.get("weight_pct"),
        item_type=row.get("item_type"),
    )


def display_row_planned_fraction(row: dict, as_of: date, db: Session | None) -> float:
    proxy = display_row_schedule_proxy(row)
    if not proxy.start_date and not proxy.target_date:
        return 0.0
    return phase_planned_progress_pct(proxy, as_of, db) / 100.0


def display_row_planned_week_fraction(
    row: dict,
    weight_fraction: float,
    week_start: date,
    week_end: date,
    db: Session | None,
) -> float:
    """Linear planned spread for one report week (same intent as legacy NETWORKDAYS overlap)."""
    start, end = _parse_row_date(row.get("display_start") or row.get("start_date")), _parse_row_date(
        row.get("display_end") or row.get("target_date")
    )
    if not start or not end or weight_fraction <= 0:
        return 0.0
    total = count_business_days_inclusive(start, end, db)
    if total <= 0:
        return 0.0
    overlap_start = max(start, week_start)
    overlap_end = min(end, week_end)
    if overlap_start > overlap_end:
        return 0.0
    overlap = count_business_days_inclusive(overlap_start, overlap_end, db)
    return weight_fraction * (overlap / total)


def display_row_duration_days(row: dict, db: Session | None) -> int | None:
    start, end = _parse_row_date(row.get("display_start") or row.get("start_date")), _parse_row_date(
        row.get("display_end") or row.get("target_date")
    )
    if start and end:
        return count_business_days_inclusive(start, end, db)
    dur = row.get("display_duration_days") or row.get("duration_days")
    return int(dur) if dur is not None else None


@dataclass
class ReportRowProgressContext:
    db: Session
    project: Project
    milestones: list[Milestone]
    ms_by_id: dict[int, Milestone]
    tasks_by_id: dict[str, ClickUpTaskCache]
    milestone_cache: dict[int, ClickUpTaskCache]
    caches: list[ClickUpTaskCache]


def display_row_actual_pct(ctx: ReportRowProgressContext, row: dict, as_of: date) -> float:
    """Actual % (0–100) — same path as health / weekly insights (ClickUp rollup first)."""
    from app.services.progress import row_clickup_progress_pct

    rid = row.get("id")
    if isinstance(rid, int) and rid > 0:
        m = ctx.ms_by_id.get(rid)
        if m:
            return float(
                row_clickup_progress_pct(
                    m,
                    ctx.milestones,
                    ctx.tasks_by_id,
                    ctx.milestone_cache,
                    caches=ctx.caches,
                )
                or 0
            )
    pct = row.get("clickup_progress_pct")
    if pct is not None:
        return float(pct)
    if isinstance(rid, int) and rid in ctx.ms_by_id:
        m = ctx.ms_by_id[rid]
        if (
            m.status == MilestoneStatus.done
            and m.actual_date
            and m.actual_date <= as_of
        ):
            return 100.0
    return 0.0


def _parse_row_date(val: object) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None
