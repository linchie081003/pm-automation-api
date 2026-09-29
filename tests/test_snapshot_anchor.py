from datetime import date
from types import SimpleNamespace

from app.services.schedule import snapshot_key_for_report_date


def test_snapshot_key_uses_report_date_trailing():
    project = SimpleNamespace(
        weekly_report_anchor_weekday=4,
        weekly_report_cutoff_offset_days=6,
    )
    rd, end = snapshot_key_for_report_date(project, date(2026, 10, 8))
    assert rd == date(2026, 10, 9)
    assert end == date(2026, 10, 9)
    rd2, _ = snapshot_key_for_report_date(project, date(2026, 10, 9))
    assert rd2 == date(2026, 10, 9)


def test_snapshot_key_zero_period_length():
    project = SimpleNamespace(
        weekly_report_anchor_weekday=4,
        weekly_report_cutoff_offset_days=0,
    )
    rd, end = snapshot_key_for_report_date(project, date(2026, 10, 9))
    assert rd == date(2026, 10, 9)
    assert end == date(2026, 10, 9)
