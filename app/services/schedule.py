from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Milestone,
    MilestoneStatus,
    ProgressSnapshot,
    ProgressSnapshotSource,
    Project,
    ScheduleBaseline,
    ScheduleBaselineMilestone,
)
from app.services.progress import resolve_actual_progress
from app.services.report_calendar import period_for_anchor, resolve_report_period, anchor_dates_between
from app.services.schedule_window import kickoff_milestones, schedule_bounds, scurve_allowed


def week_bounds(week_start: date) -> tuple[date, date]:
    return week_start, week_start + timedelta(days=6)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def snapshot_key_for_report_date(
    project: Project,
    on: date,
    *,
    project_start_date: date | None = None,
) -> tuple[date, date]:
    """Normalize to DB keys: (week_start, week_end) both = report_date (trailing end)."""
    from app.services.report_calendar import period_for_report_date, report_date_on_or_after

    wd = project.weekly_report_anchor_weekday % 7
    rd = on if on.weekday() == wd else report_date_on_or_after(
        on, project.weekly_report_anchor_weekday
    )
    _, report_date = period_for_report_date(
        rd,
        project.weekly_report_cutoff_offset_days,
        project_start_date,
    )
    return report_date, report_date


def snapshot_anchor_for_date(
    project: Project,
    on: date,
    *,
    project_start_date: date | None = None,
) -> tuple[date, date]:
    return snapshot_key_for_report_date(project, on, project_start_date=project_start_date)


def _progress_weight_rows(rows: list) -> list:
    """Leaf-ish rows with bobot > 0 for S-curve (exclude milestone gates)."""
    from app.models import TimelineItemType

    weighted = [r for r in rows if (getattr(r, "weight_pct", 0) or 0) > 0]
    if not weighted:
        return rows
    has_subtask = any(
        getattr(r, "item_type", None) == TimelineItemType.subtask for r in weighted
    )
    if has_subtask:
        return [r for r in weighted if getattr(r, "item_type", None) == TimelineItemType.subtask]
    has_task = any(getattr(r, "item_type", None) == TimelineItemType.task for r in weighted)
    if has_task:
        return [r for r in weighted if getattr(r, "item_type", None) == TimelineItemType.task]
    return weighted


def total_weight(rows: list) -> float:
    prog = _progress_weight_rows(rows)
    return sum(getattr(r, "weight_pct", 0) for r in prog) or 0.0


def _phase_rows_for_planned(rows: list) -> list:
    from app.models import TimelineItemType

    phases = [
        r
        for r in rows
        if getattr(r, "item_type", None) == TimelineItemType.phase
        and (getattr(r, "weight_pct", 0) or 0) > 0
    ]
    dated = [p for p in phases if getattr(p, "start_date", None) and getattr(p, "target_date", None)]
    return dated if dated else []


def _phase_total_days(row, db: Session | None = None) -> int:
    start = getattr(row, "start_date", None)
    end = getattr(row, "target_date", None)
    dur = getattr(row, "duration_days", None)
    if start and end:
        from app.services.business_calendar import count_business_days_inclusive

        return max(1, count_business_days_inclusive(start, end, db))
    if dur is not None and int(dur) > 0:
        return int(dur)
    return 1


def _phase_elapsed_days(row, as_of: date, db: Session | None = None) -> int:
    start = getattr(row, "start_date", None)
    end = getattr(row, "target_date", None)
    if not start:
        return 0
    if as_of < start:
        return 0
    if end and as_of >= end:
        return _phase_total_days(row, db)
    from app.services.business_calendar import count_business_days_inclusive

    return max(0, count_business_days_inclusive(start, as_of, db))


def phase_planned_progress_pct(row, as_of: date, db: Session | None = None) -> float:
    """Phase Planned Progress = Elapsed Days / Total Phase Days, clamped 0–100."""
    total = _phase_total_days(row, db)
    elapsed = _phase_elapsed_days(row, as_of, db)
    if total <= 0:
        return 0.0
    pct = (elapsed / total) * 100.0
    return max(0.0, min(100.0, pct))


def planned_cumulative_from_phases(rows: list, as_of: date, db: Session | None = None) -> float:
    """
    Planned Cumulative % = Σ (Phase Weight × Phase Planned Progress), dinormalisasi ke 0–100.
    """
    phases = _phase_rows_for_planned(rows)
    if not phases:
        return 0.0
    weight_sum = sum(float(getattr(phase, "weight_pct", 0) or 0) for phase in phases)
    if weight_sum <= 0:
        return 0.0
    ends = [getattr(p, "target_date", None) for p in phases]
    max_end = max((d for d in ends if d), default=None)
    if max_end and as_of >= max_end:
        return 100.0
    total = 0.0
    for phase in phases:
        weight = float(getattr(phase, "weight_pct", 0) or 0)
        prog = phase_planned_progress_pct(phase, as_of, db) / 100.0
        total += weight * prog
    return round((total / weight_sum) * 100.0, 2)


def planned_pct_as_of(rows: list, as_of: date, db: Session | None = None) -> float:
    phases = _phase_rows_for_planned(rows)
    if phases:
        return planned_cumulative_from_phases(rows, as_of, db)

    prog = _progress_weight_rows(rows)
    total = sum(getattr(r, "weight_pct", 0) for r in prog) or 0.0
    if total <= 0:
        return 0.0
    planned = sum(
        getattr(r, "weight_pct", 0)
        for r in prog
        if getattr(r, "target_date", None) and r.target_date <= as_of
    )
    return round(planned / total * 100, 2)


def planned_progress_rows(db: Session, project_id: int) -> list:
    """Milestone timeline (post kickoff) first, else current baseline, else SPH draft."""
    milestones = list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.id)
        ).all()
    )
    if milestones and any(m.target_date for m in milestones):
        return milestones
    current = get_current_baseline(db, project_id)
    if current:
        rows = list(
            db.scalars(
                select(ScheduleBaselineMilestone).where(
                    ScheduleBaselineMilestone.baseline_id == current.id
                )
            ).all()
        )
        if rows:
            return rows
    draft = get_draft_baseline(db, project_id)
    if draft:
        return list(
            db.scalars(
                select(ScheduleBaselineMilestone).where(
                    ScheduleBaselineMilestone.baseline_id == draft.id
                )
            ).all()
        )
    return milestones


def actual_pct_as_of(
    live_milestones: list[Milestone], as_of: date
) -> float:
    prog = _progress_weight_rows(live_milestones)
    total = sum(m.weight_pct for m in prog) or 0.0
    if total <= 0:
        return 0.0
    done = sum(
        m.weight_pct
        for m in prog
        if m.status == MilestoneStatus.done
        and m.actual_date
        and m.actual_date <= as_of
    )
    return round(done / total * 100, 2)


def compute_spi(actual: float, planned: float) -> float | None:
    if planned <= 0:
        return None
    return round(actual / planned, 4)


def get_current_baseline(db: Session, project_id: int) -> Optional[ScheduleBaseline]:
    return db.scalar(
        select(ScheduleBaseline)
        .where(
            ScheduleBaseline.project_id == project_id,
            ScheduleBaseline.is_current.is_(True),
            ScheduleBaseline.is_draft.is_(False),
        )
        .order_by(ScheduleBaseline.version.desc())
    )


def get_draft_baseline(db: Session, project_id: int) -> Optional[ScheduleBaseline]:
    return db.scalar(
        select(ScheduleBaseline)
        .where(
            ScheduleBaseline.project_id == project_id,
            ScheduleBaseline.is_draft.is_(True),
        )
        .order_by(ScheduleBaseline.version.desc())
    )


def next_baseline_version(db: Session, project_id: int) -> int:
    from sqlalchemy import func

    max_v = db.scalar(
        select(func.max(ScheduleBaseline.version)).where(
            ScheduleBaseline.project_id == project_id
        )
    )
    return int(max_v or 0) + 1


def get_or_revive_sph_draft_baseline(
    db: Session, project_id: int
) -> Optional[ScheduleBaseline]:
    """Active draft, or orphaned SPH draft row left by older generate logic."""
    draft = get_draft_baseline(db, project_id)
    if draft:
        return draft
    orphan = db.scalar(
        select(ScheduleBaseline)
        .where(
            ScheduleBaseline.project_id == project_id,
            ScheduleBaseline.is_current.is_(False),
            ScheduleBaseline.reason == "Draft baseline from SPH",
        )
        .order_by(ScheduleBaseline.version.desc())
    )
    if orphan:
        orphan.is_draft = True
    return orphan


def _copy_baseline_milestone_rows(
    db: Session, source_baseline_id: int, target_baseline_id: int
) -> None:
    rows = list(
        db.scalars(
            select(ScheduleBaselineMilestone)
            .where(ScheduleBaselineMilestone.baseline_id == source_baseline_id)
            .order_by(ScheduleBaselineMilestone.sort_order, ScheduleBaselineMilestone.id)
        ).all()
    )
    id_map: dict[int, int] = {}
    pending = list(rows)
    while pending:
        progress = False
        next_pending: list[ScheduleBaselineMilestone] = []
        for r in pending:
            if r.parent_id and r.parent_id not in id_map:
                next_pending.append(r)
                continue
            dup = ScheduleBaselineMilestone(
                baseline_id=target_baseline_id,
                milestone_id=r.milestone_id,
                row_key=r.row_key,
                name=r.name,
                start_date=r.start_date,
                target_date=r.target_date,
                weight_pct=r.weight_pct,
                is_payment_milestone=r.is_payment_milestone,
                duration_days=r.duration_days,
                item_type=r.item_type,
                parent_id=id_map.get(r.parent_id) if r.parent_id else None,
                sort_order=r.sort_order,
            )
            db.add(dup)
            db.flush()
            id_map[r.id] = dup.id
            progress = True
        if not progress:
            break
        pending = next_pending


def promote_draft_baseline(db: Session, project_id: int, effective_from: date, user_id: int | None) -> ScheduleBaseline:
    draft = get_draft_baseline(db, project_id)
    if not draft:
        return create_initial_baseline(
            db, project_id, effective_from, user_id, "Baseline at delivery start"
        )
    for b in db.scalars(
        select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)
    ):
        b.is_current = False
    promoted = ScheduleBaseline(
        project_id=project_id,
        version=next_baseline_version(db, project_id),
        effective_from=effective_from,
        reason="Promoted from SPH draft at delivery",
        is_current=True,
        is_draft=False,
        created_by_id=user_id,
    )
    db.add(promoted)
    db.flush()
    _copy_baseline_milestone_rows(db, draft.id, promoted.id)
    return promoted


def baseline_for_date(
    db: Session, project_id: int, as_of: date
) -> Optional[ScheduleBaseline]:
    """Baseline that was effective on as_of (latest with effective_from <= as_of)."""
    return db.scalar(
        select(ScheduleBaseline)
        .where(
            ScheduleBaseline.project_id == project_id,
            ScheduleBaseline.effective_from <= as_of,
        )
        .order_by(ScheduleBaseline.version.desc())
    )


def copy_milestones_to_baseline(
    db: Session,
    baseline: ScheduleBaseline,
    milestones: list[Milestone],
) -> None:
    for m in milestones:
        db.add(
            ScheduleBaselineMilestone(
                baseline_id=baseline.id,
                milestone_id=m.id,
                name=m.name,
                target_date=m.target_date,
                weight_pct=m.weight_pct,
            )
        )


def create_initial_baseline(
    db: Session,
    project_id: int,
    effective_from: date,
    created_by_id: Optional[int],
    reason: str = "Initial baseline",
) -> ScheduleBaseline:
    existing = get_current_baseline(db, project_id)
    if existing:
        return existing

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    baseline = ScheduleBaseline(
        project_id=project_id,
        version=1,
        effective_from=effective_from,
        reason=reason,
        is_current=True,
        created_by_id=created_by_id,
    )
    db.add(baseline)
    db.flush()
    copy_milestones_to_baseline(db, baseline, milestones)
    return baseline


def rebaseline_project(
    db: Session,
    project_id: int,
    effective_from: date,
    reason: str,
    created_by_id: Optional[int],
) -> ScheduleBaseline:
    current = get_current_baseline(db, project_id)
    if not current:
        return create_initial_baseline(
            db, project_id, effective_from, created_by_id, reason or "Initial baseline"
        )

    next_version = current.version + 1
    for b in db.scalars(
        select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)
    ):
        b.is_current = False

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    baseline = ScheduleBaseline(
        project_id=project_id,
        version=next_version,
        effective_from=effective_from,
        reason=reason,
        is_current=True,
        created_by_id=created_by_id,
    )
    db.add(baseline)
    db.flush()
    copy_milestones_to_baseline(db, baseline, milestones)
    return baseline


def save_weekly_progress(
    db: Session,
    project_id: int,
    week_start: date,
    source: ProgressSnapshotSource,
    weekly_report_id: Optional[int] = None,
) -> ProgressSnapshot:
    project = db.get(Project, project_id)
    if project:
        from app.services.report_calendar import ensure_weekly_period_has_started

        week_start = ensure_weekly_period_has_started(
            project.weekly_report_anchor_weekday,
            project.weekly_report_cutoff_offset_days,
            week_start,
        )
        from app.services.report_calendar import ensure_snapshot_active_week_only

        week_start = ensure_snapshot_active_week_only(
            project.weekly_report_anchor_weekday,
            project.weekly_report_cutoff_offset_days,
            week_start,
        )
        from app.services.schedule_window import project_report_start_date

        pstart = project_report_start_date(db, project_id)
        week_start, week_end = snapshot_key_for_report_date(
            project, week_start, project_start_date=pstart
        )
    else:
        week_start, week_end = week_bounds(week_start)

    existing = db.scalar(
        select(ProgressSnapshot).where(
            ProgressSnapshot.project_id == project_id,
            ProgressSnapshot.week_start == week_start,
        )
    )

    as_of = week_end

    baseline = baseline_for_date(db, project_id, as_of)
    if not baseline:
        baseline = create_initial_baseline(
            db, project_id, week_start, None, "Auto baseline at first weekly save"
        )
        db.flush()

    plan_rows = kickoff_milestones(db, project_id) or planned_progress_rows(db, project_id)
    live = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    planned = planned_pct_as_of(plan_rows, as_of, db=db)
    actual = (
        resolve_actual_progress(db, project, as_of)
        if project
        else actual_pct_as_of(live, as_of)
    )
    spi = compute_spi(actual, planned)
    spi_val = spi if spi is not None else 0.0

    if existing:
        if source in (
            ProgressSnapshotSource.manual_save,
            ProgressSnapshotSource.weekly_report,
        ):
            existing.planned_cumulative_pct = planned
            existing.actual_cumulative_pct = actual
            existing.spi_at_week = spi_val
            existing.week_end = week_end
            existing.baseline_version = baseline.version
            existing.source = source
            if weekly_report_id is not None:
                existing.weekly_report_id = weekly_report_id
        return existing

    snap = ProgressSnapshot(
        project_id=project_id,
        week_start=week_start,
        week_end=week_end,
        baseline_version=baseline.version,
        planned_cumulative_pct=planned,
        actual_cumulative_pct=actual,
        spi_at_week=spi_val,
        source=source,
        weekly_report_id=weekly_report_id,
    )
    db.add(snap)
    db.flush()
    return snap


def seed_planned_weekly_targets(db: Session, project_id: int) -> dict:
    """
    Generate daftar target planned % per anchor weekly report s.d. End proyek (planned → 100%).
    """
    from app.services.schedule_window import project_report_end_date, project_report_start_date
    from app.services.weekly_report_anchors import anchor_target_series

    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Proyek tidak ditemukan")
    project_end, end_source = project_report_end_date(db, project_id)
    if not project_end:
        raise ValueError("End proyek belum tersedia — konfirmasi timeline kick off terlebih dahulu.")
    anchors, series, _ = anchor_target_series(db, project, cap_at=project_end)
    range_start = date.fromisoformat(series[0]["anchor_date"]) if series else None
    project_start = project_report_start_date(db, project)
    if not project_start:
        raise ValueError(
            "Project start belum tersedia — isi tanggal start proyek di tab Timeline."
        )
    if not range_start or not anchors:
        raise ValueError("Tidak dapat menghitung anchor weekly report setelah project start.")

    plan_rows = kickoff_milestones(db, project_id) or planned_progress_rows(db, project_id)
    if not plan_rows:
        raise ValueError("Timeline / bobot milestone belum tersedia untuk planned S-curve.")

    baseline = get_current_baseline(db, project_id)
    if not baseline:
        baseline = create_initial_baseline(
            db,
            project_id,
            range_start,
            None,
            "Auto baseline for planned weekly targets",
        )
        db.flush()

    created = 0
    updated = 0
    out: list[dict] = []
    for row in series:
        anchor = date.fromisoformat(row.get("report_date") or row["anchor_date"])
        cut_off = date.fromisoformat(row["cut_off_date"])
        if cut_off > anchor:
            pass  # legacy forward row: keep stored week_end
        else:
            cut_off = anchor
        planned = float(row["planned_cumulative_pct"])
        existing = db.scalar(
            select(ProgressSnapshot).where(
                ProgressSnapshot.project_id == project_id,
                ProgressSnapshot.week_start == anchor,
            )
        )
        if existing:
            existing.planned_cumulative_pct = planned
            existing.week_end = cut_off
            existing.baseline_version = baseline.version
            if existing.source == ProgressSnapshotSource.planned_target:
                existing.actual_cumulative_pct = 0.0
                existing.spi_at_week = 0.0
            updated += 1
        else:
            db.add(
                ProgressSnapshot(
                    project_id=project_id,
                    week_start=anchor,
                    week_end=cut_off,
                    baseline_version=baseline.version,
                    planned_cumulative_pct=planned,
                    actual_cumulative_pct=0.0,
                    spi_at_week=0.0,
                    source=ProgressSnapshotSource.planned_target,
                )
            )
            created += 1
        out.append(
            {
                "anchor_date": anchor.isoformat(),
                "cut_off_date": cut_off.isoformat(),
                "planned_cumulative_pct": planned,
            }
        )
    return {
        "anchors": out,
        "count": len(out),
        "created": created,
        "updated": updated,
        "range_start": range_start.isoformat(),
        "project_start_date": project_start.isoformat(),
        "schedule_end": project_end.isoformat(),
        "bounds_source": end_source,
    }


def scurve_points(
    db: Session,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    project = db.get(Project, project_id)
    from app.services.schedule_window import project_report_end_date, reports_scurve_available

    if not project or not reports_scurve_available(db, project):
        return []

    from app.services.progress import resolve_actual_progress
    from app.services.weekly_report_anchors import anchor_target_series

    project_end, _ = project_report_end_date(db, project_id)
    if not project_end:
        return []

    today = date.today()
    try:
        anchors, target_rows, _ = anchor_target_series(db, project, cap_at=project_end)
    except ValueError:
        return []
    if not anchors or not target_rows:
        return []

    target_by_anchor: dict[str, dict] = {}
    for r in target_rows:
        key = r.get("report_date") or r["anchor_date"]
        target_by_anchor[key] = r
    range_start = date_from if date_from is not None else anchors[0]
    range_end = date_to if date_to is not None else anchors[-1]
    snapshots = db.scalars(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.week_start)
    ).all()
    snap_by_week = {s.week_start: s for s in snapshots}
    current = get_current_baseline(db, project_id)

    points: list[dict] = []
    for anchor in anchors:
        if anchor < range_start or anchor > range_end:
            continue
        row = target_by_anchor.get(anchor.isoformat())
        if not row:
            continue
        snap = snap_by_week.get(anchor)
        if snap and snap.source == ProgressSnapshotSource.planned_target:
            planned = float(snap.planned_cumulative_pct)
        else:
            planned = float(row["planned_cumulative_pct"])
        cut_off = date.fromisoformat(row["cut_off_date"])
        frozen = False
        baseline_version = current.version if current else None

        if snap and snap.source in (
            ProgressSnapshotSource.weekly_report,
            ProgressSnapshotSource.manual_save,
        ):
            actual = snap.actual_cumulative_pct
            spi = snap.spi_at_week
            baseline_version = snap.baseline_version
            frozen = True
        elif cut_off <= today:
            metrics_as_of = min(today, cut_off)
            actual = resolve_actual_progress(db, project, metrics_as_of)
            spi = compute_spi(actual, planned) or 0.0
        else:
            actual = 0.0
            spi = 0.0

        points.append(
            {
                "date": anchor.isoformat(),
                "cut_off_date": cut_off.isoformat(),
                "planned_pct": planned,
                "actual_pct": actual,
                "baseline_version": baseline_version,
                "is_frozen": frozen,
                "spi": spi,
            }
        )

    return points


def milestone_chart_points(
    db: Session,
    project_id: int,
    as_of: date | None = None,
) -> dict:
    """Actual vs target (planned) per phase/milestone untuk grafik milestone."""
    from app.models import ClickUpTaskCache, Milestone, MilestoneStatus, TimelineItemType
    from app.services.progress import (
        build_clickup_lookups,
        clickup_status_mapping_context,
        row_clickup_progress_pct,
    )
    from app.services.progress_metrics import active_report_week_context
    from app.services.schedule_window import kickoff_milestones

    project = db.get(Project, project_id)
    if not project:
        return {
            "as_of": None,
            "cut_off_date": None,
            "status_date_report": None,
            "active_report_date": None,
            "active_anchor_date": None,
            "active_period_start": None,
            "items": [],
        }

    report_date, period_start, cut_off, metrics_as_of = active_report_week_context(
        project, as_of=as_of, db=db
    )
    anchor = report_date
    plan_rows = kickoff_milestones(db, project_id) or planned_progress_rows(db, project_id)
    if not plan_rows:
        return {
            "as_of": metrics_as_of.isoformat(),
            "cut_off_date": cut_off.isoformat(),
            "status_date_report": metrics_as_of.isoformat(),
            "active_report_date": anchor.isoformat(),
            "active_anchor_date": anchor.isoformat(),
            "active_period_start": period_start.isoformat(),
            "items": [],
        }

    milestones = list(
        db.scalars(
            select(Milestone).where(Milestone.project_id == project_id)
        ).all()
    )
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)

    phases = _phase_rows_for_planned(plan_rows)
    items: list[dict] = []
    with clickup_status_mapping_context(db):
        if phases:
            for phase in sorted(phases, key=lambda p: (p.sort_order or 0, p.id or 0)):
                actual = row_clickup_progress_pct(
                    phase, milestones, tasks_by_id, milestone_cache, caches=caches
                )
                items.append(
                    {
                        "id": phase.id,
                        "name": phase.name,
                        "weight_pct": round(float(phase.weight_pct or 0), 2),
                        "planned_pct": round(
                            phase_planned_progress_pct(phase, metrics_as_of, db), 2
                        ),
                        "actual_pct": round(float(actual or 0), 2),
                    }
                )
        else:
            prog = _progress_weight_rows(plan_rows)
            for m in sorted(prog, key=lambda x: (x.sort_order or 0, x.id or 0)):
                target = getattr(m, "target_date", None)
                planned = 100.0 if target and target <= metrics_as_of else 0.0
                if m.item_type == TimelineItemType.phase:
                    actual = row_clickup_progress_pct(
                        m, milestones, tasks_by_id, milestone_cache, caches=caches
                    )
                elif m.clickup_task_id or milestone_cache.get(m.id):
                    actual = row_clickup_progress_pct(
                        m, milestones, tasks_by_id, milestone_cache, caches=caches
                    )
                elif m.status == MilestoneStatus.done and m.actual_date and m.actual_date <= metrics_as_of:
                    actual = 100.0
                else:
                    actual = 0.0
                items.append(
                    {
                        "id": m.id,
                        "name": m.name,
                        "weight_pct": round(float(m.weight_pct or 0), 2),
                        "planned_pct": round(planned, 2),
                        "actual_pct": round(float(actual or 0), 2),
                    }
                )

    return {
        "as_of": metrics_as_of.isoformat(),
        "cut_off_date": cut_off.isoformat(),
        "status_date_report": metrics_as_of.isoformat(),
        "active_report_date": anchor.isoformat(),
        "active_anchor_date": anchor.isoformat(),
        "active_period_start": period_start.isoformat(),
        "items": items,
    }


def rebaseline_markers(db: Session, project_id: int) -> list[dict]:
    baselines = db.scalars(
        select(ScheduleBaseline)
        .where(ScheduleBaseline.project_id == project_id)
        .order_by(ScheduleBaseline.version)
    ).all()
    return [
        {
            "version": b.version,
            "effective_from": b.effective_from.isoformat(),
            "reason": b.reason,
            "is_current": b.is_current,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in baselines
    ]
