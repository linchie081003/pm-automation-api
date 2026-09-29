"""Weekly report anchor windows (project start → schedule end)."""
from datetime import date

from sqlalchemy.orm import Session

from app.models import Project
from app.services.report_calendar import (
    anchor_on_or_before,
    extend_anchors_for_project_end,
    filter_anchors_through_active_week,
    first_schedule_anchor_date,
    period_for_anchor,
    weekly_report_anchor_dates,
)
from app.services.schedule import planned_pct_as_of, planned_progress_rows
from app.services.schedule_window import (
    kickoff_milestones,
    project_report_end_date,
    project_report_start_date,
)


def resolve_project_anchor_context(
    db: Session,
    project: Project,
    *,
    cap_at: date | None = None,
) -> tuple[list[date], date | None, date | None, date | None, str]:
    """
    Returns (anchors, range_start, project_start, project_end, end_source).
    Anchor = hari laporan mingguan pada/ setelah project start.
    """
    project_start = project_report_start_date(db, project)
    project_end, end_source = project_report_end_date(db, project.id)
    anchors, range_start = weekly_report_anchor_dates(
        project_start,
        project_end,
        project.weekly_report_anchor_weekday,
        project.weekly_report_first_anchor_date,
        cap_at=cap_at,
    )
    anchors = extend_anchors_for_project_end(
        anchors,
        project_end,
        project.weekly_report_cutoff_offset_days,
    )
    return anchors, range_start, project_start, project_end, end_source


def started_weekly_report_anchors(
    db: Session,
    project: Project,
    *,
    cap_at: date | None = None,
) -> tuple[list[date], date | None, date | None, date | None, str]:
    """Anchor weekly report dari awal s.d. minggu yang sudah berjalan (tidak termasuk masa depan)."""
    from datetime import date as date_cls

    anchors, range_start, project_start, project_end, end_source = (
        resolve_project_anchor_context(db, project, cap_at=cap_at)
    )
    today = date_cls.today()
    anchors = filter_anchors_through_active_week(
        anchors, project.weekly_report_anchor_weekday, today
    )
    return anchors, range_start, project_start, project_end, end_source


def planned_cumulative_at_cutoff(db: Session, project_id: int, cut_off: date) -> float:
    """
    Planned kumulatif weekly report:
    Σ Phase Weight × (Elapsed Days / Total Phase Days) per phase, cut-off = as-of.
    """
    plan_rows = kickoff_milestones(db, project_id) or planned_progress_rows(db, project_id)
    if not plan_rows:
        return 0.0
    return planned_pct_as_of(plan_rows, cut_off, db=db)


def anchor_rows_with_planned(
    db: Session,
    project: Project,
    anchors: list[date],
    *,
    project_end: date | None = None,
    today: date | None = None,
) -> list[dict]:
    from datetime import date as date_cls

    today = today or date_cls.today()
    active_anchor = anchor_on_or_before(today, project.weekly_report_anchor_weekday)
    last_i = len(anchors) - 1
    out: list[dict] = []
    project_start = project_report_start_date(db, project)
    for i, rd in enumerate(anchors):
        period_start, report_date = period_for_anchor(
            rd,
            project.weekly_report_cutoff_offset_days,
            project_start,
        )
        as_of = report_date
        status_date = report_date
        if i == last_i and project_end:
            as_of = project_end
            status_date = project_end
        elif rd == active_anchor and today < report_date:
            as_of = today
        planned = planned_cumulative_at_cutoff(db, project.id, as_of)
        out.append(
            {
                "report_date": report_date.isoformat(),
                "period_start": period_start.isoformat(),
                "anchor_date": report_date.isoformat(),
                "cut_off_date": status_date.isoformat(),
                "planned_cumulative_pct": planned,
            }
        )
    return out


def anchor_target_series(
    db: Session,
    project: Project,
    *,
    cap_at: date | None = None,
) -> tuple[list[date], list[dict], date | None]:
    """Satu sumber untuk Target weekly report, S-curve planned, dan export."""
    project_end, _ = project_report_end_date(db, project.id)
    end_cap = cap_at if cap_at is not None else project_end
    anchors, _, _, _, _ = resolve_project_anchor_context(db, project, cap_at=end_cap)
    if not anchors:
        return [], [], project_end
    rows = anchor_rows_with_planned(
        db, project, anchors, project_end=project_end or end_cap
    )
    return anchors, rows, project_end
