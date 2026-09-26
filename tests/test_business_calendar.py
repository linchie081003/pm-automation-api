from datetime import date

from app.services.business_calendar import count_business_days_inclusive


def test_count_business_days_inclusive_oct_2026():
    # Thu 1 Oct – Wed 14 Oct 2026: weekends 3–4 and 10–11 → 10 hari kerja
    start = date(2026, 10, 1)
    end = date(2026, 10, 14)
    assert count_business_days_inclusive(start, end, db=None) == 10
