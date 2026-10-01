from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import IntegrationSettings


def _holiday_set(db: Session | None) -> set[date]:
    if not db:
        return set()
    row = db.query(IntegrationSettings).first()
    if not row or not row.holiday_dates:
        return set()
    out: set[date] = set()
    for raw in row.holiday_dates:
        iso: str | None = None
        if isinstance(raw, str) and raw.strip():
            iso = raw.strip()[:10]
        elif isinstance(raw, dict) and raw.get("date"):
            iso = str(raw["date"])[:10]
        if iso:
            try:
                out.add(date.fromisoformat(iso))
            except ValueError:
                continue
    return out


def _work_weekdays(db: Session | None) -> set[int]:
    if not db:
        return {0, 1, 2, 3, 4}
    row = db.query(IntegrationSettings).first()
    if not row or not row.work_weekdays:
        return {0, 1, 2, 3, 4}
    return {int(x) for x in row.work_weekdays if 0 <= int(x) <= 6}


def is_business_day(d: date, holidays: set[date], work_days: set[int]) -> bool:
    return d.weekday() in work_days and d not in holidays


def next_business_day(d: date, holidays: set[date], work_days: set[int]) -> date:
    cur = d
    while not is_business_day(cur, holidays, work_days):
        cur += timedelta(days=1)
    return cur


def add_business_days(start: date, days: int, db: Session | None = None) -> date:
    """Inclusive span: duration 1 => same business day as start."""
    holidays = _holiday_set(db)
    work_days = _work_weekdays(db)
    if days <= 0:
        return next_business_day(start, holidays, work_days)
    cur = next_business_day(start, holidays, work_days)
    remaining = days - 1
    while remaining > 0:
        cur += timedelta(days=1)
        if is_business_day(cur, holidays, work_days):
            remaining -= 1
    return cur


def business_day_after(d: date, db: Session | None = None) -> date:
    holidays = _holiday_set(db)
    work_days = _work_weekdays(db)
    cur = d + timedelta(days=1)
    while not is_business_day(cur, holidays, work_days):
        cur += timedelta(days=1)
    return cur


def subtract_business_days(end: date, days: int, db: Session | None = None) -> date:
    """Start of an inclusive business-day span of length `days` ending on `end`."""
    holidays = _holiday_set(db)
    work_days = _work_weekdays(db)
    cur = next_business_day(end, holidays, work_days)
    if days <= 1:
        return cur
    remaining = days - 1
    while remaining > 0:
        cur -= timedelta(days=1)
        if is_business_day(cur, holidays, work_days):
            remaining -= 1
    return cur


def count_business_days_inclusive(
    start: date, end: date, db: Session | None = None
) -> int:
    """Inclusive count of business days between start and end (matches draft timeline durasi)."""
    if end < start:
        start, end = end, start
    holidays = _holiday_set(db)
    work_days = _work_weekdays(db)
    count = 0
    cur = start
    while cur <= end:
        if is_business_day(cur, holidays, work_days):
            count += 1
        cur += timedelta(days=1)
    return count
