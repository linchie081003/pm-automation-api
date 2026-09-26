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
