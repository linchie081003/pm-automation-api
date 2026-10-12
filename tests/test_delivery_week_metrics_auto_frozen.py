from datetime import date
from unittest.mock import MagicMock, patch

from app.models import ProgressSnapshotSource
from app.services.progress_metrics import delivery_week_metrics


def test_past_period_uses_auto_frozen_snapshot():
    project = MagicMock()
    project.id = 1
    cut_off = date(2026, 9, 26)
    anchor = cut_off

    frozen = MagicMock()
    frozen.source = ProgressSnapshotSource.auto_frozen
    frozen.planned_cumulative_pct = 44.0
    frozen.actual_cumulative_pct = 33.0

    db = MagicMock()

    with patch("app.services.progress_metrics.today_jakarta", return_value=date(2026, 10, 1)), patch(
        "app.services.schedule.ensure_closed_period_snapshot", return_value=frozen
    ), patch(
        "app.services.progress_metrics.snapshot_for_anchor_week", return_value=frozen
    ), patch("app.services.progress_metrics.kickoff_milestones", return_value=[MagicMock()]), patch(
        "app.services.progress_metrics.active_report_week_context",
        return_value=(anchor, cut_off, cut_off, cut_off),
    ), patch("app.services.progress_metrics.resolve_actual_progress", return_value=99.0) as live:
        planned, actual, _, _, source = delivery_week_metrics(db, project)

    assert actual == 33.0
    assert planned == 44.0
    assert source == "weekly_snapshot"
    live.assert_not_called()
