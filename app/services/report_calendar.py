"""Weekly report calendar: report_date (end of period) + trailing period_start."""
from datetime import date, timedelta


def report_date_on_or_before(d: date, weekday: int) -> date:
    """Most recent report weekday on or before d."""
    wd = weekday % 7
    return d - timedelta(days=(d.weekday() - wd) % 7)


def report_date_on_or_after(d: date, weekday: int) -> date:
    wd = weekday % 7
    delta = (wd - d.weekday()) % 7
    return d + timedelta(days=delta)


# Deprecated aliases (internal callers migrating)
anchor_on_or_before = report_date_on_or_before
anchor_on_or_after = report_date_on_or_after


def active_open_report_date(d: date, weekday: int) -> date:
    """
    Trailing: report_date (akhir periode) untuk minggu yang sedang berjalan.
    Periode closed setelah report_date lewat; sebelum itu pakai report_date berikutnya ≥ d.
    """
    return report_date_on_or_after(d, weekday)


def display_period_day_count(period_length_days: int) -> int:
    """Inclusive day count for UI (period_length_days=6 → 7 hari kalender)."""
    return max(1, int(period_length_days) + 1)


def first_period_report_date(
    project_start_date: date | None,
    report_weekday: int | None,
    explicit_first_report_date: date | None = None,
) -> date | None:
    if explicit_first_report_date is not None:
        return explicit_first_report_date
    if project_start_date is not None and report_weekday is not None:
        return first_schedule_report_date(project_start_date, report_weekday)
    return None


def period_for_report_date(
    report_date: date,
    period_length_days: int,
    project_start_date: date | None = None,
    *,
    report_weekday: int | None = None,
    explicit_first_report_date: date | None = None,
) -> tuple[date, date]:
    """
    Trailing period: period_start .. report_date (inclusive).
    Periode pertama (report_date = first anchor): project_start .. report_date.
    Periode berikutnya: report_date - period_length_days .. report_date (clamp ≥ project_start).
    """
    first_rd = first_period_report_date(
        project_start_date, report_weekday, explicit_first_report_date
    )
    if project_start_date is not None and first_rd is not None and report_date == first_rd:
        return project_start_date, report_date

    raw_start = report_date - timedelta(days=period_length_days)
    if project_start_date is not None and raw_start < project_start_date:
        period_start = project_start_date
    else:
        period_start = raw_start
    return period_start, report_date


def period_for_anchor(
    anchor: date,
    cutoff_offset_days: int,
    project_start_date: date | None = None,
    *,
    report_weekday: int | None = None,
    explicit_first_report_date: date | None = None,
) -> tuple[date, date]:
    """Returns (period_start, report_date). `anchor` is the report_date."""
    return period_for_report_date(
        anchor,
        cutoff_offset_days,
        project_start_date,
        report_weekday=report_weekday,
        explicit_first_report_date=explicit_first_report_date,
    )


def report_dates_between(
    start: date,
    end: date,
    weekday: int,
    *,
    cap_at: date | None = None,
) -> list[date]:
    if start > end:
        return []
    cap = cap_at or end
    if cap < start:
        return []
    end_eff = min(end, cap)
    first = report_date_on_or_after(start, weekday)
    if first > end_eff:
        return []
    out: list[date] = []
    cur = first
    while cur <= end_eff:
        out.append(cur)
        cur += timedelta(days=7)
    return out


anchor_dates_between = report_dates_between


def next_report_date_from_today(today: date, weekday: int) -> date:
    return active_open_report_date(today, weekday)


next_anchor_from_today = next_report_date_from_today


def calendar_report_date_window(
    today: date,
    weekday: int,
    *,
    weeks_back: int = 12,
    weeks_forward: int = 12,
) -> list[date]:
    start = today - timedelta(days=7 * weeks_back)
    end = today + timedelta(days=7 * weeks_forward)
    return report_dates_between(start, end, weekday, cap_at=end)


calendar_anchor_window = calendar_report_date_window


def nearest_report_date_on_or_before(dates: list[date], today: date) -> date | None:
    if not dates:
        return None
    past = [a for a in dates if a <= today]
    if past:
        return past[-1]
    return dates[0]


nearest_anchor_on_or_before = nearest_report_date_on_or_before


def first_schedule_report_date(project_start: date | None, weekday: int) -> date | None:
    if not project_start:
        return None
    return report_date_on_or_after(project_start, weekday)


first_schedule_anchor_date = first_schedule_report_date


def validate_first_report_date(
    first_report: date,
    project_start: date | None,
    weekday: int,
) -> None:
    min_rd = first_schedule_report_date(project_start, weekday)
    if min_rd and first_report < min_rd:
        raise ValueError(
            "Tanggal laporan pertama harus ≥ tanggal laporan pertama setelah project start "
            f"({min_rd.isoformat()})."
        )
    if first_report.weekday() != weekday % 7:
        raise ValueError("Tanggal laporan pertama harus jatuh pada hari laporan (report weekday).")


validate_weekly_first_anchor_date = validate_first_report_date


def weekly_report_range_start(
    project_start: date | None,
    weekday: int,
    first_report_date: date | None,
) -> date | None:
    if first_report_date is not None:
        validate_first_report_date(first_report_date, project_start, weekday)
        return first_report_date
    return first_schedule_report_date(project_start, weekday)


weekly_anchor_range_start = weekly_report_range_start


def weekly_report_dates(
    project_start: date | None,
    project_end: date | None,
    weekday: int,
    first_report_date: date | None,
    *,
    cap_at: date | None = None,
) -> tuple[list[date], date | None]:
    if not project_end:
        return [], None
    range_start = weekly_report_range_start(project_start, weekday, first_report_date)
    if not range_start:
        range_start = first_schedule_report_date(project_start, weekday)
    if not range_start:
        return [], None
    end_cap = project_end
    if cap_at is not None and cap_at < end_cap:
        end_cap = cap_at
    dates = report_dates_between(range_start, project_end, weekday, cap_at=end_cap)
    return dates, range_start


weekly_report_anchor_dates = weekly_report_dates


def filter_report_dates_through_active_week(
    report_dates: list[date],
    weekday: int,
    today: date | None = None,
) -> list[date]:
    if not report_dates:
        return []
    today = today or date.today()
    active = active_open_report_date(today, weekday)
    if report_dates[0] > active:
        return []
    return [d for d in report_dates if d <= active]


filter_anchors_through_active_week = filter_report_dates_through_active_week


def extend_report_dates_for_project_end(
    report_dates: list[date],
    project_end: date | None,
    period_length_days: int,
) -> list[date]:
    """Extend until last report_date covers project_end (trailing: report_date >= end)."""
    if not report_dates or not project_end:
        return report_dates
    out = list(report_dates)
    while True:
        last_report = out[-1]
        if last_report >= project_end:
            break
        next_rd = out[-1] + timedelta(days=7)
        if next_rd in out:
            break
        out.append(next_rd)
    return out


extend_anchors_for_project_end = extend_report_dates_for_project_end


def _normalize_report_date(
    report_weekday: int,
    report_date: date | None,
    today: date,
) -> date:
    rd = report_date or next_report_date_from_today(today, report_weekday)
    rd = report_date_on_or_before(rd, report_weekday)
    if rd.weekday() != report_weekday % 7:
        rd = report_date_on_or_before(rd, report_weekday)
    return rd


def resolve_report_period(
    report_weekday: int,
    period_length_days: int,
    report_date: date | None,
    today: date | None = None,
    *,
    project_start_date: date | None = None,
    explicit_first_report_date: date | None = None,
) -> tuple[date, date, date]:
    """
    Returns (report_date, period_start, status_date).
    status_date == report_date (trailing end of period; alias for legacy cut_off).
    """
    today = today or date.today()
    rd = _normalize_report_date(report_weekday, report_date, today)
    period_start, end = period_for_report_date(
        rd,
        period_length_days,
        project_start_date,
        report_weekday=report_weekday,
        explicit_first_report_date=explicit_first_report_date,
    )
    return end, period_start, end


def ensure_snapshot_active_week_only(
    report_weekday: int,
    period_length_days: int,
    report_date: date,
    today: date | None = None,
) -> date:
    today = today or date.today()
    active_rd = active_open_report_date(today, report_weekday)
    rd, _, _ = resolve_report_period(
        report_weekday,
        period_length_days,
        report_date,
        today,
    )
    if rd < active_rd:
        raise ValueError(
            "Progress snapshot tidak dapat digenerate untuk periode yang sudah lewat — "
            f"pilih minggu aktif ({active_rd.isoformat()})."
        )
    if rd > active_rd:
        raise ValueError(
            "Progress snapshot belum tersedia untuk periode mendatang — "
            f"minggu aktif: {active_rd.isoformat()}."
        )
    return rd


def ensure_weekly_period_has_started(
    report_weekday: int,
    period_length_days: int,
    report_date: date,
    today: date | None = None,
) -> date:
    today = today or date.today()
    active_rd = active_open_report_date(today, report_weekday)
    rd, period_start, _ = resolve_report_period(
        report_weekday,
        period_length_days,
        report_date,
        today,
        project_start_date=None,
    )
    if rd > active_rd:
        raise ValueError(
            "Periode weekly report belum dimulai — pilih tanggal laporan pada atau sebelum "
            f"minggu aktif ({active_rd.isoformat()})."
        )
    if today < period_start:
        raise ValueError(
            "Periode weekly report belum dimulai — periode dimulai "
            f"{period_start.isoformat()}."
        )
    return rd
