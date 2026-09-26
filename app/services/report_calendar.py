"""Per-project weekly report anchor dates (weekday + cut-off offset)."""
from datetime import date, timedelta


def anchor_on_or_before(d: date, weekday: int) -> date:
    """Most recent date on `weekday` (0=Mon) on or before d."""
    wd = weekday % 7
    return d - timedelta(days=(d.weekday() - wd) % 7)


def anchor_on_or_after(d: date, weekday: int) -> date:
    wd = weekday % 7
    delta = (wd - d.weekday()) % 7
    return d + timedelta(days=delta)


def period_for_anchor(anchor: date, cutoff_offset_days: int) -> tuple[date, date]:
    """Returns (period_start, cut_off_date). period_start = anchor; cut_off = anchor + offset."""
    cut_off = anchor + timedelta(days=cutoff_offset_days)
    if cut_off < anchor:
        period_start = cut_off
        return period_start, anchor
    return anchor, cut_off


def anchor_dates_between(
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
    first = anchor_on_or_after(start, weekday)
    if first > end_eff:
        return []
    out: list[date] = []
    cur = first
    while cur <= end_eff:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def next_anchor_from_today(today: date, weekday: int) -> date:
    """Upcoming anchor (includes today if today is anchor day)."""
    prev = anchor_on_or_before(today, weekday)
    if prev == today:
        return today
    return prev + timedelta(days=7)


def calendar_anchor_window(
    today: date,
    weekday: int,
    *,
    weeks_back: int = 12,
    weeks_forward: int = 12,
) -> list[date]:
    """Anchor dates on weekday around today when project schedule window unknown."""
    start = today - timedelta(days=7 * weeks_back)
    end = today + timedelta(days=7 * weeks_forward)
    return anchor_dates_between(start, end, weekday, cap_at=end)


def nearest_anchor_on_or_before(anchors: list[date], today: date) -> date | None:
    if not anchors:
        return None
    past = [a for a in anchors if a <= today]
    if past:
        return past[-1]
    return anchors[0]


def first_schedule_anchor_date(project_start: date | None, weekday: int) -> date | None:
    """Anchor weekly report pertama pada / setelah project start."""
    if not project_start:
        return None
    return anchor_on_or_after(project_start, weekday)


def validate_weekly_first_anchor_date(
    first_weekly: date,
    project_start: date | None,
    weekday: int,
) -> None:
    min_anchor = first_schedule_anchor_date(project_start, weekday)
    if min_anchor and first_weekly < min_anchor:
        raise ValueError(
            "Tanggal weekly report pertama harus ≥ anchor pertama setelah project start "
            f"({min_anchor.isoformat()})."
        )
    if first_weekly.weekday() != weekday % 7:
        raise ValueError("Tanggal weekly report pertama harus jatuh pada hari anchor laporan.")


def weekly_anchor_range_start(
    project_start: date | None,
    weekday: int,
    first_weekly: date | None,
) -> date | None:
    """Tanggal mulai rentang anchor weekly report (setelah project start)."""
    if first_weekly is not None:
        validate_weekly_first_anchor_date(first_weekly, project_start, weekday)
        return first_weekly
    return first_schedule_anchor_date(project_start, weekday)


def weekly_report_anchor_dates(
    project_start: date | None,
    project_end: date | None,
    weekday: int,
    first_weekly: date | None,
    *,
    cap_at: date | None = None,
) -> tuple[list[date], date | None]:
    """Daftar tanggal anchor; planned S-curve dihitung per cut-off tiap anchor."""
    if not project_end:
        return [], None
    range_start = weekly_anchor_range_start(project_start, weekday, first_weekly)
    if not range_start:
        range_start = first_schedule_anchor_date(project_start, weekday)
    if not range_start:
        return [], None
    end_cap = project_end
    if cap_at is not None and cap_at < end_cap:
        end_cap = cap_at
    anchors = anchor_dates_between(range_start, project_end, weekday, cap_at=end_cap)
    return anchors, range_start


def filter_anchors_through_active_week(
    anchors: list[date],
    weekday: int,
    today: date | None = None,
) -> list[date]:
    """Hanya anchor weekly report yang sudah dimulai (≤ minggu laporan aktif)."""
    if not anchors:
        return []
    today = today or date.today()
    active = anchor_on_or_before(today, weekday)
    if anchors[0] > active:
        return []
    return [a for a in anchors if a <= active]


def extend_anchors_for_project_end(
    anchors: list[date],
    project_end: date | None,
    cutoff_offset_days: int,
) -> list[date]:
    """
    Tambah satu periode anchor berikutnya jika cut-off anchor terakhir belum mencapai end proyek
    (termasuk bila end proyek jatuh sebelum tanggal anchor minggu berikutnya).
    """
    if not anchors or not project_end:
        return anchors
    out = list(anchors)
    while True:
        _, last_cut = period_for_anchor(out[-1], cutoff_offset_days)
        if last_cut >= project_end:
            break
        next_anchor = out[-1] + timedelta(days=7)
        if next_anchor in out:
            break
        out.append(next_anchor)
    return out


def ensure_snapshot_active_week_only(
    project_anchor_weekday: int,
    cutoff_offset_days: int,
    anchor_date: date,
    today: date | None = None,
) -> date:
    """Progress snapshot hanya untuk minggu laporan aktif (bukan periode yang sudah lewat)."""
    today = today or date.today()
    active_anchor = anchor_on_or_before(today, project_anchor_weekday)
    anchor, _, _ = resolve_report_period(
        project_anchor_weekday,
        cutoff_offset_days,
        anchor_date,
        today,
    )
    if anchor < active_anchor:
        raise ValueError(
            "Progress snapshot tidak dapat digenerate untuk periode yang sudah lewat — "
            f"pilih minggu aktif ({active_anchor.isoformat()})."
        )
    if anchor > active_anchor:
        raise ValueError(
            "Progress snapshot belum tersedia untuk periode mendatang — "
            f"minggu aktif: {active_anchor.isoformat()}."
        )
    return anchor


def ensure_weekly_period_has_started(
    project_anchor_weekday: int,
    cutoff_offset_days: int,
    anchor_date: date,
    today: date | None = None,
) -> date:
    """
    Weekly report / snapshot hanya untuk periode yang sudah dimulai (anchor ≤ minggu aktif).
    """
    today = today or date.today()
    active_anchor = anchor_on_or_before(today, project_anchor_weekday)
    anchor, _, _ = resolve_report_period(
        project_anchor_weekday,
        cutoff_offset_days,
        anchor_date,
        today,
    )
    if anchor > active_anchor:
        raise ValueError(
            "Periode weekly report belum dimulai — pilih tanggal anchor pada atau sebelum "
            f"minggu aktif ({active_anchor.isoformat()})."
        )
    return anchor


def resolve_report_period(
    project_anchor_weekday: int,
    cutoff_offset_days: int,
    anchor_date: date | None,
    today: date | None = None,
) -> tuple[date, date, date]:
    """Returns (anchor, period_start, cut_off)."""
    today = today or date.today()
    anchor = anchor_date or next_anchor_from_today(today, project_anchor_weekday)
    anchor = anchor_on_or_before(anchor, project_anchor_weekday)
    if anchor.weekday() != project_anchor_weekday % 7:
        anchor = anchor_on_or_before(anchor, project_anchor_weekday)
    period_start, cut_off = period_for_anchor(anchor, cutoff_offset_days)
    return anchor, period_start, cut_off
