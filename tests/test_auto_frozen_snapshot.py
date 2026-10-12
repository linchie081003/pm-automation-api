from datetime import date
from unittest.mock import MagicMock, patch

from app.models import ProgressSnapshotSource
from app.services.schedule import ensure_closed_period_snapshot


def test_ensure_closed_period_skips_active_period():
    project = MagicMock()
    project.id = 1
    db = MagicMock()
    week_end = date(2099, 1, 1)
    with patch("app.services.schedule.today_jakarta", return_value=date(2026, 1, 1)):
        assert (
            ensure_closed_period_snapshot(db, project, date(2025, 12, 25), week_end) is None
        )
    db.scalar.assert_not_called()


def test_ensure_closed_period_freezes_past_period():
    project = MagicMock()
    project.id = 1
    db = MagicMock()
    db.scalar.return_value = None
    week_start = date(2026, 9, 26)
    week_end = date(2026, 10, 2)

    with patch("app.services.schedule.today_jakarta", return_value=date(2026, 10, 5)), patch(
        "app.services.schedule.baseline_for_date", return_value=MagicMock(version=1)
    ), patch("app.services.schedule.kickoff_milestones", return_value=[MagicMock()]), patch(
        "app.services.schedule.planned_pct_as_of", return_value=30.0
    ), patch(
        "app.services.schedule.resolve_actual_progress", return_value=25.0
    ), patch(
        "app.services.schedule.compute_spi", return_value=0.83
    ):
        snap = ensure_closed_period_snapshot(db, project, week_start, week_end)

    assert snap is not None
    assert snap.source == ProgressSnapshotSource.auto_frozen
    assert snap.actual_cumulative_pct == 25.0
    db.add.assert_called_once()
