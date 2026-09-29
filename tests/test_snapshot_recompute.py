from datetime import date
from unittest.mock import MagicMock

from app.services.snapshot_recompute import (
    is_legacy_forward_snapshot,
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
