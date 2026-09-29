"""Recompute / migrate progress snapshots & weekly reports to trailing report_date keys."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgressSnapshot, ProgressSnapshotSource, Project, WeeklyReport
from app.services.progress import resolve_actual_progress
from app.services.schedule import (
    baseline_for_date,
    compute_spi,
    planned_pct_as_of,
    snapshot_key_for_report_date,
)
from app.services.schedule_window import kickoff_milestones, project_report_start_date

SOURCE_PRIORITY: dict[ProgressSnapshotSource, int] = {
    ProgressSnapshotSource.weekly_report: 3,
    ProgressSnapshotSource.manual_save: 2,
    ProgressSnapshotSource.planned_target: 1,
}


def is_legacy_forward_snapshot(week_start: date, week_end: date) -> bool:
    return week_end > week_start


def trailing_report_date_for_row(
    week_start: date,
    week_end: date,
    project: Project,
    project_start: date | None,
) -> date:
    """
    Forward histori: week_start = awal periode, week_end = cut-off (= report_date trailing).
    Trailing / sudah normal: week_start = report_date (dinormalisasi ke hari laporan).
    """
    if is_legacy_forward_snapshot(week_start, week_end):
        return week_end
    rd, _ = snapshot_key_for_report_date(
        project, week_start, project_start_date=project_start
    )
    return rd


def _pick_keep_snapshot(a: ProgressSnapshot, b: ProgressSnapshot) -> ProgressSnapshot:
    pa = SOURCE_PRIORITY.get(a.source, 0)
    pb = SOURCE_PRIORITY.get(b.source, 0)
    if pa != pb:
        return a if pa > pb else b
    return a if (a.id or 0) >= (b.id or 0) else b


def _merge_snapshot_fields(keep: ProgressSnapshot, drop: ProgressSnapshot) -> None:
    if drop.weekly_report_id and not keep.weekly_report_id:
        keep.weekly_report_id = drop.weekly_report_id
    if SOURCE_PRIORITY.get(drop.source, 0) > SOURCE_PRIORITY.get(keep.source, 0):
        keep.source = drop.source
        keep.planned_cumulative_pct = drop.planned_cumulative_pct
        keep.actual_cumulative_pct = drop.actual_cumulative_pct
        keep.spi_at_week = drop.spi_at_week
        keep.baseline_version = drop.baseline_version


def recompute_snapshot_metrics(
    db: Session,
    project: Project,
    snap: ProgressSnapshot,
    report_date: date,
) -> None:
    plan_rows = kickoff_milestones(db, project.id)
    if not plan_rows:
        from app.services.schedule import planned_progress_rows

        plan_rows = planned_progress_rows(db, project.id)
    as_of = report_date
    planned = planned_pct_as_of(plan_rows, as_of, db=db) if plan_rows else 0.0
    if snap.source == ProgressSnapshotSource.planned_target:
        snap.planned_cumulative_pct = planned
        snap.actual_cumulative_pct = 0.0
        snap.spi_at_week = 0.0
    else:
        actual = resolve_actual_progress(db, project, as_of)
        spi = compute_spi(actual, planned)
        snap.planned_cumulative_pct = planned
        snap.actual_cumulative_pct = actual
        snap.spi_at_week = spi if spi is not None else 0.0
    baseline = baseline_for_date(db, project.id, as_of)
    if baseline:
        snap.baseline_version = baseline.version
    snap.week_start = report_date
    snap.week_end = report_date


def recompute_project_historical_snapshots(
    db: Session,
    project_id: int,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    1) Pindahkan kunci legacy forward (week_end > week_start) → week_start = report_date.
    2) Normalisasi week_end = week_start.
    3) Hitung ulang planned/actual/SPI per report_date (termasuk weekly_report / manual_save).
    4) Selaraskan WeeklyReport week_start/week_end + frozen_metrics dari snapshot terkait.
    """
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    pstart = project_report_start_date(db, project)
    stats: dict[str, Any] = {
        "project_id": project_id,
        "snapshots_seen": 0,
        "keys_migrated": 0,
        "snapshots_merged": 0,
        "snapshots_recomputed": 0,
        "weekly_reports_migrated": 0,
        "weekly_reports_updated": 0,
        "dry_run": dry_run,
    }

    snaps = list(
        db.scalars(
            select(ProgressSnapshot)
            .where(ProgressSnapshot.project_id == project_id)
            .order_by(ProgressSnapshot.week_start, ProgressSnapshot.id)
        ).all()
    )
    stats["snapshots_seen"] = len(snaps)

    target_map: dict[date, ProgressSnapshot] = {}
    to_delete: list[ProgressSnapshot] = []

    for snap in snaps:
        rd = trailing_report_date_for_row(snap.week_start, snap.week_end, project, pstart)
        if snap.week_start != rd:
            stats["keys_migrated"] += 1
        if rd not in target_map:
            target_map[rd] = snap
            continue
        keep = _pick_keep_snapshot(target_map[rd], snap)
        drop = snap if keep is target_map[rd] else target_map[rd]
        _merge_snapshot_fields(keep, drop)
        to_delete.append(drop)
        target_map[rd] = keep
        stats["snapshots_merged"] += 1

    if not dry_run:
        for snap in to_delete:
            db.delete(snap)
        db.flush()

    for rd, snap in sorted(target_map.items(), key=lambda x: x[0]):
        if dry_run:
            stats["snapshots_recomputed"] += 1
            continue
        recompute_snapshot_metrics(db, project, snap, rd)
        stats["snapshots_recomputed"] += 1

    reports = list(
        db.scalars(
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project_id)
            .order_by(WeeklyReport.week_start)
        ).all()
    )
    report_by_key: dict[date, WeeklyReport] = {}
    reports_to_delete: list[WeeklyReport] = []

    for report in reports:
        rd = trailing_report_date_for_row(report.week_start, report.week_end, project, pstart)
        if report.week_start != rd:
            stats["weekly_reports_migrated"] += 1
        if rd in report_by_key:
            keep = report_by_key[rd]
            if (report.generated_at or report.id) > (keep.generated_at or keep.id):
                reports_to_delete.append(keep)
                report_by_key[rd] = report
            else:
                reports_to_delete.append(report)
            continue
        report_by_key[rd] = report

    if not dry_run:
        for rep in reports_to_delete:
            db.delete(rep)
        db.flush()

    snap_by_week = {s.week_start: s for s in target_map.values()}

    for rd, report in report_by_key.items():
        if dry_run:
            stats["weekly_reports_updated"] += 1
            continue
        report.week_start = rd
        report.week_end = rd
        snap = snap_by_week.get(rd)
        if snap:
            snap.weekly_report_id = report.id
            frozen = dict(report.frozen_metrics or {})
            frozen.update(
                {
                    "planned_pct": snap.planned_cumulative_pct,
                    "actual_pct": snap.actual_cumulative_pct,
                    "spi": snap.spi_at_week,
                }
            )
            report.frozen_metrics = frozen
        stats["weekly_reports_updated"] += 1

    return stats


def recompute_all_projects(db: Session, *, dry_run: bool = False) -> list[dict[str, Any]]:
    ids = list(db.scalars(select(Project.id).order_by(Project.id)).all())
    out: list[dict[str, Any]] = []
    for pid in ids:
        out.append(recompute_project_historical_snapshots(db, pid, dry_run=dry_run))
    return out
