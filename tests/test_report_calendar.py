from datetime import date

import pytest

from app.services.report_calendar import (
    active_open_report_date,
    display_period_day_count,
    extend_anchors_for_project_end,
    ensure_snapshot_active_week_only,
    ensure_weekly_period_has_started,
    filter_anchors_through_active_week,
    period_for_report_date,
    validate_first_report_date,
    weekly_report_range_start,
)


def test_first_report_must_be_on_report_weekday():
    with pytest.raises(ValueError, match="hari laporan"):
        validate_first_report_date(
            date(2026, 10, 1),
            date(2026, 9, 1),
            4,
        )


def test_first_report_must_be_after_schedule_first():
    with pytest.raises(ValueError, match="≥"):
        validate_first_report_date(
            date(2026, 10, 1),
            date(2026, 10, 2),
            4,
        )


def test_trailing_period_for_report_date():
    rd = date(2026, 10, 2)  # Friday
    ps, end = period_for_report_date(rd, 6, None)
    assert end == rd
    assert ps == date(2026, 9, 26)


def test_trailing_period_clamped_to_project_start():
    rd = date(2026, 10, 2)
    ps, end = period_for_report_date(rd, 6, date(2026, 9, 28))
    assert ps == date(2026, 9, 28)
    assert end == rd


def test_display_period_day_count_inclusive():
    assert display_period_day_count(6) == 7
    assert display_period_day_count(0) == 1


def test_extend_report_dates_until_end_covered():
    anchors = [date(2026, 12, 3), date(2026, 12, 10)]
    project_end = date(2026, 12, 15)
    extended = extend_anchors_for_project_end(anchors, project_end, 6)
    assert extended[-1] == date(2026, 12, 17)


def test_extend_when_end_before_next_weekly():
    anchors = [date(2026, 12, 10)]
    project_end = date(2026, 12, 12)
    extended = extend_anchors_for_project_end(anchors, project_end, 0)
    assert extended == [date(2026, 12, 10), date(2026, 12, 17)]


def test_filter_report_dates_includes_open_trailing_period():
    dates = [date(2026, 9, 25), date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)]
    # 2026-09-29 = Selasa; weekday 4 (Jumat) → periode terbuka report_date 2026-10-02
    started = filter_anchors_through_active_week(dates, 4, date(2026, 9, 29))
    assert started == [date(2026, 9, 25), date(2026, 10, 2)]


def test_active_open_report_date_trailing():
    # Selasa 29 Sep → report_date aktif = Jumat 2 Okt
    assert active_open_report_date(date(2026, 9, 29), 4) == date(2026, 10, 2)


def test_ensure_snapshot_rejects_past_week():
    with pytest.raises(ValueError, match="sudah lewat"):
        ensure_snapshot_active_week_only(4, 0, date(2026, 9, 18), today=date(2026, 9, 25))


def test_ensure_weekly_period_rejects_future():
    with pytest.raises(ValueError, match="belum dimulai"):
        ensure_weekly_period_has_started(4, 0, date(2026, 10, 16), today=date(2026, 10, 9))
    ok = ensure_weekly_period_has_started(4, 0, date(2026, 10, 9), today=date(2026, 10, 9))
    assert ok == date(2026, 10, 9)


def test_weekly_range_start_with_first_report():
    start = weekly_report_range_start(
        date(2026, 10, 1),
        4,
        date(2026, 10, 9),
    )
    assert start == date(2026, 10, 9)
