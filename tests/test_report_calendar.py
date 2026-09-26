from datetime import date

import pytest

from app.services.report_calendar import (
    anchor_dates_between,
    extend_anchors_for_project_end,
    ensure_snapshot_active_week_only,
    ensure_weekly_period_has_started,
    filter_anchors_through_active_week,
    validate_weekly_first_anchor_date,
    weekly_anchor_range_start,
)


def test_first_weekly_must_be_on_anchor_weekday():
    with pytest.raises(ValueError, match="hari anchor"):
        validate_weekly_first_anchor_date(
            date(2026, 10, 1),  # Kamis
            date(2026, 9, 1),
            4,  # Jumat
        )


def test_first_weekly_must_be_after_schedule_first_anchor():
    with pytest.raises(ValueError, match="≥"):
        validate_weekly_first_anchor_date(
            date(2026, 10, 1),  # Kamis
            date(2026, 10, 2),  # mulai jadwal Jumat → anchor pertama 2 Okt
            4,  # Jumat
        )


def test_generate_anchors_through_project_end():
    start = weekly_anchor_range_start(
        date(2026, 10, 1),
        4,
        date(2026, 10, 9),
    )
    assert start == date(2026, 10, 9)
    anchors = anchor_dates_between(start, date(2026, 10, 30), 4, cap_at=date(2026, 10, 30))
    assert anchors[0] == date(2026, 10, 9)
    assert anchors[-1] == date(2026, 10, 30)


def test_extend_anchor_when_end_after_last_cutoff():
    anchors = [date(2026, 12, 3), date(2026, 12, 10)]
    project_end = date(2026, 12, 15)
    extended = extend_anchors_for_project_end(anchors, project_end, 0)
    assert extended[-1] == date(2026, 12, 17)


def test_extend_anchor_when_end_before_next_weekly_date():
    anchors = [date(2026, 12, 10)]
    project_end = date(2026, 12, 12)
    extended = extend_anchors_for_project_end(anchors, project_end, 0)
    assert extended == [date(2026, 12, 10), date(2026, 12, 17)]


def test_filter_anchors_excludes_future_weeks():
    anchors = [date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 15)]
    # Kamis = 3
    started = filter_anchors_through_active_week(anchors, 3, date(2026, 10, 9))
    assert started == [date(2026, 10, 1), date(2026, 10, 8)]


def test_ensure_snapshot_rejects_past_week():
    import pytest

    with pytest.raises(ValueError, match="sudah lewat"):
        ensure_snapshot_active_week_only(4, 0, date(2026, 9, 18), today=date(2026, 9, 25))


def test_ensure_weekly_period_rejects_future_anchor():
    import pytest

    with pytest.raises(ValueError, match="belum dimulai"):
        ensure_weekly_period_has_started(4, 0, date(2026, 10, 16), today=date(2026, 10, 9))
    ok = ensure_weekly_period_has_started(4, 0, date(2026, 10, 9), today=date(2026, 10, 9))
    assert ok == date(2026, 10, 9)
