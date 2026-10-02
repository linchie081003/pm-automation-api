from datetime import date
from unittest.mock import MagicMock, patch

from app.models import ProgressSnapshotSource
from app.services.progress_metrics import delivery_week_metrics


def test_active_week_uses_target_snapshot_and_live_actual():
    project = MagicMock()
    project.id = 1
    project.weekly_report_anchor_weekday = 4  # Jumat
    project.weekly_report_cutoff_offset_days = 0

    snap = MagicMock()
    snap.source = ProgressSnapshotSource.planned_target
    snap.planned_cumulative_pct = 92.5
    snap.actual_cumulative_pct = 0.0

    db = MagicMock()

    with patch("app.services.progress_metrics.snapshot_for_anchor_week", return_value=snap), patch(
        "app.services.progress_metrics.kickoff_milestones", return_value=[MagicMock()]
    ), patch(
        "app.services.progress_metrics.active_report_week_context",
        return_value=(date(2026, 9, 26), date(2026, 9, 26), date(2026, 9, 26), date(2026, 9, 26)),
    ), patch(
        "app.services.progress_metrics.resolve_actual_progress", return_value=41.2
    ) as live:
        planned, actual, cut_off, anchor, source = delivery_week_metrics(db, project)

    assert planned == 92.5
    assert actual == 41.2
    live.assert_called_once_with(db, project, date(2026, 9, 26))
    assert cut_off == date(2026, 9, 26)
    assert source == "active_week_live"


def test_in_progress_week_planned_uses_metrics_as_of_not_future_cutoff():
    project = MagicMock()
    project.id = 1
    project.weekly_report_anchor_weekday = 4
    project.weekly_report_cutoff_offset_days = 6
    project.weekly_report_first_anchor_date = None

    db = MagicMock()
    today = date(2026, 9, 29)
    future_cutoff = date(2026, 10, 2)

    with patch("app.services.progress_metrics.snapshot_for_anchor_week", return_value=None), patch(
        "app.services.progress_metrics.kickoff_milestones", return_value=[MagicMock()]
    ), patch(
        "app.services.progress_metrics.active_report_week_context",
        return_value=(future_cutoff, date(2026, 9, 26), future_cutoff, today),
    ), patch(
        "app.services.progress_metrics.planned_pct_as_of", return_value=12.0
    ) as planned_fn, patch(
        "app.services.progress_metrics.resolve_actual_progress", return_value=5.0
    ) as live:
        planned, actual, cut_off, _, source = delivery_week_metrics(db, project)

    planned_fn.assert_called_once()
    assert planned_fn.call_args[0][1] == today
    assert planned == 12.0
    assert actual == 5.0
    assert cut_off == future_cutoff
    assert source == "active_week_live"
    live.assert_called_once_with(db, project, today)
