from datetime import date
from unittest.mock import MagicMock, patch

from app.models import ProgressSnapshot, ProgressSnapshotSource
from app.services.snapshot_recompute import (
    FROZEN_SOURCES,
    is_legacy_forward_snapshot,
    recompute_snapshot_metrics,
    trailing_report_date_for_row,
)


def test_legacy_forward_detection():
    assert is_legacy_forward_snapshot(date(2026, 9, 26), date(2026, 10, 2))
    assert not is_legacy_forward_snapshot(date(2026, 10, 2), date(2026, 10, 2))


def test_trailing_report_date_from_legacy_forward():
    project = MagicMock()
    project.weekly_report_anchor_weekday = 4
    project.weekly_report_cutoff_offset_days = 6
    rd = trailing_report_date_for_row(
        date(2026, 9, 26),
        date(2026, 10, 2),
        project,
        date(2026, 9, 1),
    )
    assert rd == date(2026, 10, 2)


def test_trailing_report_date_normalizes_key():
    project = MagicMock()
    project.weekly_report_anchor_weekday = 4  # Friday
    project.weekly_report_cutoff_offset_days = 6
    rd = trailing_report_date_for_row(
        date(2026, 10, 1),  # Thursday — bukan hari laporan
        date(2026, 10, 1),
        project,
        None,
    )
    assert rd == date(2026, 10, 2)


def test_frozen_sources_tuple():
    assert ProgressSnapshotSource.weekly_report in FROZEN_SOURCES
    assert ProgressSnapshotSource.manual_save in FROZEN_SOURCES


def test_recompute_skips_actual_for_frozen_weekly_report_snapshot():
    project = MagicMock()
    project.id = 1
    snap = ProgressSnapshot(
        project_id=1,
        week_start=date(2026, 9, 26),
        week_end=date(2026, 10, 2),
        planned_cumulative_pct=55.0,
        actual_cumulative_pct=44.0,
        spi_at_week=0.8,
        source=ProgressSnapshotSource.weekly_report,
        baseline_version=1,
    )
    db = MagicMock()
    with patch("app.services.snapshot_recompute.kickoff_milestones") as km:
        recompute_snapshot_metrics(db, project, snap, date(2026, 10, 2))
        km.assert_not_called()
    assert snap.planned_cumulative_pct == 55.0
    assert snap.actual_cumulative_pct == 44.0
    assert snap.spi_at_week == 0.8
    assert snap.week_start == date(2026, 10, 2)
    assert snap.week_end == date(2026, 10, 2)


def test_recompute_skips_actual_for_frozen_manual_save_snapshot():
    project = MagicMock()
    project.id = 2
    snap = ProgressSnapshot(
        project_id=2,
        week_start=date(2026, 10, 1),
        week_end=date(2026, 10, 1),
        planned_cumulative_pct=70.0,
        actual_cumulative_pct=68.5,
        spi_at_week=0.9786,
        source=ProgressSnapshotSource.manual_save,
        baseline_version=2,
    )
    db = MagicMock()
    with patch("app.services.snapshot_recompute.kickoff_milestones") as km:
        recompute_snapshot_metrics(db, project, snap, date(2026, 10, 2))
        km.assert_not_called()
    assert snap.planned_cumulative_pct == 70.0
    assert snap.actual_cumulative_pct == 68.5
    assert snap.spi_at_week == 0.9786
    assert snap.week_start == date(2026, 10, 2)
    assert snap.week_end == date(2026, 10, 2)
